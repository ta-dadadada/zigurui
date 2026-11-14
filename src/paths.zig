//! XDG保存先の解決を担当する。解決だけではディレクトリを作成しない。
const std = @import("std");

const index_filename = "default.idx";

/// XDGの各保存先と組み込み既定索引。文字列は呼び出し元のarenaが所有する。
pub const Paths = struct {
    config: []const u8,
    data: []const u8,
    cache: []const u8,
    state: []const u8,
    index: []const u8,

    /// 設定ファイルの絶対パスを返す。返す文字列はallocatorが所有する。
    pub fn configFile(allocator: std.mem.Allocator, env: *const std.process.Environ.Map) ![]const u8 {
        const config = try directory(allocator, env, "XDG_CONFIG_HOME", ".config");
        return std.fs.path.resolveAlloc(allocator, &.{ config, "config.json" });
    }

    /// 既定索引の絶対パスを返す。返す文字列はallocatorが所有する。
    pub fn defaultIndex(allocator: std.mem.Allocator, env: *const std.process.Environ.Map) ![]const u8 {
        const data = try directory(allocator, env, "XDG_DATA_HOME", ".local/share");
        return std.fs.path.join(allocator, &.{ data, index_filename });
    }

    /// 返す文字列はallocatorが所有する。呼び出し元のarenaで一括解放する。
    pub fn resolve(allocator: std.mem.Allocator, env: *const std.process.Environ.Map) !Paths {
        const data = try directory(allocator, env, "XDG_DATA_HOME", ".local/share");
        return .{
            .config = try directory(allocator, env, "XDG_CONFIG_HOME", ".config"),
            .data = data,
            .cache = try directory(allocator, env, "XDG_CACHE_HOME", ".cache"),
            .state = try directory(allocator, env, "XDG_STATE_HOME", ".local/state"),
            .index = try std.fs.path.join(allocator, &.{ data, index_filename }),
        };
    }
};

fn directory(allocator: std.mem.Allocator, env: *const std.process.Environ.Map, key: []const u8, fallback: []const u8) ![]const u8 {
    if (env.get(key)) |value| {
        if (std.fs.path.isAbsolute(value)) return std.fs.path.join(allocator, &.{ value, "zigurui" });
    }
    const home = env.get("HOME") orelse return error.HomeDirectoryUnavailable;
    if (!std.fs.path.isAbsolute(home)) return error.HomeDirectoryUnavailable;
    return std.fs.path.join(allocator, &.{ home, fallback, "zigurui" });
}
