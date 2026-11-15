//! 継続利用するCLI既定値の読み込みと検証を担当する。設定ファイルは書き換えない。
const std = @import("std");
const relevance = @import("relevance.zig");
const ranking = @import("ranking.zig");
const search = @import("search.zig");

/// CLIで指定しなかった項目の既定値。index_pathは解決済みの絶対パス。
pub const Config = struct {
    mode: search.Mode = .literal,
    rank: ranking.Order = .path,
    relevance: relevance.Settings = .{},
    index_path: ?[]const u8 = null,
};

/// 戻り値と診断文はallocatorが所有し、呼び出し元のarenaで一括解放する。
/// fileがnull、またはファイルが存在しない場合は組み込み既定値を返す。
pub fn load(allocator: std.mem.Allocator, io: std.Io, file: ?[]const u8, diagnostic: *?[]const u8) !Config {
    const path = file orelse return .{};
    const bytes = std.Io.Dir.cwd().readFileAlloc(io, path, allocator, .limited(64 * 1024)) catch |err| switch (err) {
        error.FileNotFound => return .{},
        else => {
            diagnostic.* = try std.fmt.allocPrint(allocator, "設定 {s}: 読み込めません ({s})", .{ path, @errorName(err) });
            return error.InvalidConfiguration;
        },
    };
    return parse(allocator, bytes, path, diagnostic) catch |err| {
        if (diagnostic.* == null) diagnostic.* = try std.fmt.allocPrint(allocator, "設定 {s}: 不正なJSONです ({s})", .{ path, @errorName(err) });
        return err;
    };
}

fn invalid(allocator: std.mem.Allocator, diagnostic: *?[]const u8, path: []const u8, key: []const u8, reason: []const u8) !Config {
    diagnostic.* = try std.fmt.allocPrint(allocator, "設定 {s}: {s}: {s}", .{ path, key, reason });
    return error.InvalidConfiguration;
}

// JSONの借用文字列と解決済みパスを含め、確保は呼び出し元のarenaが保持する。
fn parse(allocator: std.mem.Allocator, bytes: []const u8, path: []const u8, diagnostic: *?[]const u8) !Config {
    const root = try std.json.parseFromSliceLeaky(std.json.Value, allocator, bytes, .{ .duplicate_field_behavior = .@"error" });
    if (root != .object) return invalid(allocator, diagnostic, path, "全体", "JSONオブジェクトが必要です");
    var config: Config = .{};
    var entries = root.object.iterator();
    while (entries.next()) |entry| {
        const key = entry.key_ptr.*;
        const value = entry.value_ptr.*;
        if (std.mem.eql(u8, key, "rank")) {
            if (value != .string) return invalid(allocator, diagnostic, path, key, "順位名の文字列が必要です");
            config.rank = std.meta.stringToEnum(ranking.Order, value.string) orelse return invalid(allocator, diagnostic, path, key, "path/heading/relevanceを指定してください");
            continue;
        }
        if (std.mem.eql(u8, key, "relevance")) {
            config.relevance = parseRelevance(allocator, value) catch |err| {
                if (err == error.OutOfMemory) return err;
                return invalid(allocator, diagnostic, path, key, "fieldまたは重みの設定が不正です");
            };
            continue;
        }
        if (!std.mem.eql(u8, key, "mode") and !std.mem.eql(u8, key, "index_path")) return invalid(allocator, diagnostic, path, key, "未知のキーです");
        if (value != .string) return invalid(allocator, diagnostic, path, key, "文字列が必要です（nullも使用できません）");
        if (std.mem.eql(u8, key, "mode")) {
            config.mode = if (std.mem.eql(u8, value.string, "literal")) .literal else if (std.mem.eql(u8, value.string, "normalized")) .normalized else return invalid(allocator, diagnostic, path, key, "literalまたはnormalizedを指定してください");
        } else {
            if (value.string.len == 0 or std.mem.indexOfScalar(u8, value.string, 0) != null or !std.unicode.utf8ValidateSlice(value.string)) return invalid(allocator, diagnostic, path, key, "空文字列・NUL・不正なUTF-8は使用できません");
            // 相対パスは実行ディレクトリではなく設定ファイルの親を基準にする。
            config.index_path = try std.fs.path.resolveAlloc(allocator, &.{ std.fs.path.dirname(path).?, value.string });
        }
    }
    return config;
}

fn number(value: std.json.Value) !f64 {
    return switch (value) {
        .integer => |v| @floatFromInt(v),
        .float => |v| v,
        else => error.InvalidRelevanceConfiguration,
    };
}
fn parseRelevance(a: std.mem.Allocator, value: std.json.Value) !relevance.Settings {
    if (value != .object) return error.InvalidRelevanceConfiguration;
    var result: relevance.Settings = .{};
    var entries = value.object.iterator();
    while (entries.next()) |entry| {
        const key = entry.key_ptr.*;
        const v = entry.value_ptr.*;
        if (std.mem.eql(u8, key, "proximity_weight")) result.proximity_weight = try number(v) else if (std.mem.eql(u8, key, "ordered_weight")) result.ordered_weight = try number(v) else if (std.mem.eql(u8, key, "fields")) {
            if (v != .array or v.array.items.len == 0 or v.array.items.len > relevance.max_fields) return error.InvalidRelevanceConfiguration;
            const fields = try a.alloc(relevance.Field, v.array.items.len);
            for (v.array.items, fields) |raw, *field| {
                if (raw != .object) return error.InvalidRelevanceConfiguration;
                const kind = raw.object.get("kind") orelse return error.InvalidRelevanceConfiguration;
                if (kind != .string) return error.InvalidRelevanceConfiguration;
                field.* = .{ .kind = std.meta.stringToEnum(relevance.Kind, kind.string) orelse return error.InvalidRelevanceConfiguration };
                var props = raw.object.iterator();
                while (props.next()) |prop| {
                    if (std.mem.eql(u8, prop.key_ptr.*, "weight")) field.weight = try number(prop.value_ptr.*) else if (std.mem.eql(u8, prop.key_ptr.*, "pointer")) {
                        if (prop.value_ptr.* != .string) return error.InvalidRelevanceConfiguration;
                        field.pointer = prop.value_ptr.string;
                    } else if (!std.mem.eql(u8, prop.key_ptr.*, "kind")) return error.InvalidRelevanceConfiguration;
                }
            }
            result.fields = fields;
        } else return error.InvalidRelevanceConfiguration;
    }
    try result.validate();
    return result;
}
