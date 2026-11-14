//! YAML frontmatterの限定プロファイル、型付き絞り込み、スキーマ集計と本文投影を担う。
//! 保存索引は変更せず、本文は原文のUTF-8バイト列を借用する。
const std = @import("std");
const search = @import("search.zig");
const Allocator = std.mem.Allocator;

pub const max_header_bytes = 256 * 1024;
pub const max_depth = 16;
pub const max_nodes = 4096;
pub const max_pointer_bytes = 4096;
pub const max_schema_paths = 4096;
pub const max_filters = 64;
const Error = error{ OutOfMemory, InvalidFrontmatter, UnsupportedFrontmatter, FrontmatterTooLarge, FrontmatterNumberOutOfRange, InvalidFilter };

/// 文書配列の添字だけを保持する。パスや本文を所有せず、reasonは元のエラー。
pub const Diagnostic = struct {
    document_index: usize,
    context: enum { frontmatter, schema_aggregation },
    reason: Error,
};

fn recordDiagnostic(out: ?*?Diagnostic, index: usize, context: @FieldType(Diagnostic, "context"), reason: Error) void {
    if (out) |value| value.* = .{ .document_index = index, .context = context, .reason = reason };
}

extern fn zigurui_yaml_new(input: [*]const u8, length: usize) ?*anyopaque;
extern fn zigurui_yaml_next(parser: *anyopaque, event: *Event) c_int;
extern fn zigurui_yaml_delete(parser: *anyopaque) void;
const Event = extern struct { kind: c_int = 0, value: ?[*]const u8 = null, length: usize = 0, plain: c_int = 0 };

/// stringだけを所有する。deinitは生成時と同じallocatorで行う。
pub const Scalar = union(enum) {
    null: void,
    boolean: bool,
    integer: i64,
    float: f64,
    string: []u8,

    fn deinit(self: *Scalar, allocator: Allocator) void {
        if (self.* == .string) allocator.free(self.string);
    }

    fn equal(self: Scalar, other: Scalar) bool {
        if (std.meta.activeTag(self) != std.meta.activeTag(other)) return false;
        return switch (self) {
            .null => true,
            .boolean => |v| v == other.boolean,
            .integer => |v| v == other.integer,
            .float => |v| v == other.float,
            .string => |v| std.mem.eql(u8, v, other.string),
        };
    }
};

const Value = union(enum) {
    scalar: Scalar,
    array: []Scalar,
    object: void,

    fn deinit(self: *Value, allocator: Allocator) void {
        switch (self.*) {
            .scalar => |*v| v.deinit(allocator),
            .array => |items| {
                for (items) |*item| item.deinit(allocator);
                allocator.free(items);
            },
            .object => {},
        }
    }

    fn matches(self: Value, expected: Scalar) bool {
        return switch (self) {
            .scalar => |v| v.equal(expected),
            .array => |items| for (items) |item| {
                if (item.equal(expected)) break true;
            } else false,
            .object => false,
        };
    }
};

const Field = struct { pointer: []u8, value: Value };

/// fieldsを所有する。body_startは元の文書のUTF-8バイト位置。
pub const Metadata = struct {
    fields: std.ArrayList(Field) = .empty,
    body_start: usize = 0,
    has_frontmatter: bool = false,

    pub fn deinit(self: *Metadata, allocator: Allocator) void {
        for (self.fields.items) |*field| {
            allocator.free(field.pointer);
            field.value.deinit(allocator);
        }
        self.fields.deinit(allocator);
    }

    fn find(self: *const Metadata, pointer: []const u8) ?Value {
        for (self.fields.items) |field| {
            if (std.mem.eql(u8, field.pointer, pointer)) return field.value;
        }
        return null;
    }

    /// pathは保存snapshotまたはscanで収集した文書パスを借用する。
    pub fn matches(self: *const Metadata, filters: []const Filter, path: []const u8) bool {
        for (filters) |filter| {
            switch (filter.kind) {
                .metadata => {
                    const value = self.find(filter.pointer) orelse return false;
                    if (!value.matches(filter.expected)) return false;
                },
                .file_name, .file_path => {
                    if (filter.expected != .string) return false;
                    const value = if (filter.kind == .file_name) std.fs.path.basename(path) else path;
                    if (!std.mem.eql(u8, value, filter.expected.string)) return false;
                },
            }
        }
        return true;
    }
};

const Header = struct { payload: []const u8 = "", body_start: usize = 0, present: bool = false };

fn header(text: []const u8) Error!Header {
    const start: usize = if (std.mem.startsWith(u8, text, "\xef\xbb\xbf")) 3 else 0;
    const tail = text[start..];
    const content_start = if (std.mem.startsWith(u8, tail, "---\r\n")) start + 5 else if (std.mem.startsWith(u8, tail, "---\n")) start + 4 else if (std.mem.eql(u8, tail, "---")) text.len else return .{};
    var position = content_start;
    while (position < text.len) {
        if (position - content_start > max_header_bytes) return error.FrontmatterTooLarge;
        const remaining = text[position..];
        const limited = remaining[0..@min(remaining.len, max_header_bytes - (position - content_start) + 5)];
        const relative_end = std.mem.indexOfScalar(u8, limited, '\n') orelse blk: {
            if (limited.len != remaining.len) return error.FrontmatterTooLarge;
            break :blk limited.len;
        };
        const end = position + relative_end;
        const line = text[position..if (end < text.len and end > position and text[end - 1] == '\r') end - 1 else end];
        const next = if (end < text.len) end + 1 else end;
        if (std.mem.eql(u8, line, "---") or std.mem.eql(u8, line, "..."))
            return .{ .payload = text[content_start..position], .body_start = next, .present = true };
        if (next - content_start > max_header_bytes) return error.FrontmatterTooLarge;
        position = next;
    }
    return error.InvalidFrontmatter;
}

const NumberKind = enum { integer, float };
fn numberKind(text: []const u8) ?NumberKind {
    if (text.len == 0) return null;
    var i: usize = if (text[0] == '-') 1 else 0;
    if (i == text.len) return null;
    if (text[i] == '0') {
        i += 1;
    } else {
        if (text[i] < '1' or text[i] > '9') return null;
        while (i < text.len and std.ascii.isDigit(text[i])) : (i += 1) {}
    }
    var kind: NumberKind = .integer;
    if (i < text.len and text[i] == '.') {
        kind = .float;
        i += 1;
        const begin = i;
        while (i < text.len and std.ascii.isDigit(text[i])) : (i += 1) {}
        if (i == begin) return null;
    }
    if (i < text.len and (text[i] == 'e' or text[i] == 'E')) {
        kind = .float;
        i += 1;
        if (i < text.len and (text[i] == '+' or text[i] == '-')) i += 1;
        const begin = i;
        while (i < text.len and std.ascii.isDigit(text[i])) : (i += 1) {}
        if (i == begin) return null;
    }
    return if (i == text.len) kind else null;
}

fn number(text: []const u8, kind: NumberKind) Error!Scalar {
    if (kind == .integer) return .{ .integer = std.fmt.parseInt(i64, text, 10) catch return error.FrontmatterNumberOutOfRange };
    const value = std.fmt.parseFloat(f64, text) catch return error.FrontmatterNumberOutOfRange;
    if (!std.math.isFinite(value)) return error.FrontmatterNumberOutOfRange;
    if (value == 0) {
        // 非ゼロ仮数がゼロへunderflowする値も、文字列へ黙って変更しない。
        for (text) |byte| {
            if (byte == 'e' or byte == 'E') break;
            if (byte >= '1' and byte <= '9') return error.FrontmatterNumberOutOfRange;
        }
    }
    return .{ .float = value };
}

fn scalar(allocator: Allocator, text: []const u8, plain: bool) Error!Scalar {
    if (plain) {
        if (text.len == 0 or std.mem.eql(u8, text, "null") or std.mem.eql(u8, text, "~")) return .null;
        if (std.mem.eql(u8, text, "true")) return .{ .boolean = true };
        if (std.mem.eql(u8, text, "false")) return .{ .boolean = false };
        if (numberKind(text)) |kind| return number(text, kind);
    }
    return .{ .string = try allocator.dupe(u8, text) };
}

fn appendPointer(allocator: Allocator, prefix: []const u8, key: []const u8) Error![]u8 {
    var length = prefix.len + 1;
    for (key) |byte| length += if (byte == '~' or byte == '/') @as(usize, 2) else 1;
    if (length > max_pointer_bytes) return error.FrontmatterTooLarge;
    const result = try allocator.alloc(u8, length);
    @memcpy(result[0..prefix.len], prefix);
    result[prefix.len] = '/';
    var at = prefix.len + 1;
    for (key) |byte| {
        if (byte == '~' or byte == '/') {
            result[at] = '~';
            result[at + 1] = if (byte == '~') '0' else '1';
            at += 2;
        } else {
            result[at] = byte;
            at += 1;
        }
    }
    return result;
}

const Parser = struct {
    allocator: Allocator,
    handle: *anyopaque,
    event: Event = .{},
    nodes: usize = 0,
    result: *Metadata,

    fn next(self: *Parser) Error!void {
        switch (zigurui_yaml_next(self.handle, &self.event)) {
            1 => {},
            -2 => return error.OutOfMemory,
            -3 => return error.UnsupportedFrontmatter,
            -4 => return error.FrontmatterTooLarge,
            else => return error.InvalidFrontmatter,
        }
        if (self.event.kind == 5 or self.event.kind == 6 or self.event.kind == 8) {
            self.nodes += 1;
            if (self.nodes > max_nodes) return error.FrontmatterTooLarge;
        }
    }

    fn bytes(self: *const Parser) []const u8 {
        return if (self.event.value) |ptr| ptr[0..self.event.length] else "";
    }

    fn map(self: *Parser, prefix: []const u8, depth: usize) Error!void {
        if (depth > max_depth) return error.FrontmatterTooLarge;
        try self.next();
        while (self.event.kind != 7) {
            if (self.event.kind != 5) return error.UnsupportedFrontmatter;
            if (std.mem.eql(u8, self.bytes(), "<<")) return error.UnsupportedFrontmatter;
            const pointer = try appendPointer(self.allocator, prefix, self.bytes());
            var transferred = false;
            errdefer if (!transferred) self.allocator.free(pointer);
            if (self.result.find(pointer) != null) return error.InvalidFrontmatter;
            const index = self.result.fields.items.len;
            try self.result.fields.append(self.allocator, .{ .pointer = pointer, .value = .object });
            transferred = true;
            try self.next();
            try self.value(index, pointer, depth + 1);
        }
        try self.next();
    }

    fn value(self: *Parser, index: usize, pointer: []const u8, depth: usize) Error!void {
        if (depth > max_depth) return error.FrontmatterTooLarge;
        switch (self.event.kind) {
            5 => {
                const parsed = try scalar(self.allocator, self.bytes(), self.event.plain != 0);
                self.result.fields.items[index].value = .{ .scalar = parsed };
                try self.next();
            },
            6 => try self.map(pointer, depth),
            8 => {
                var items: std.ArrayList(Scalar) = .empty;
                errdefer {
                    for (items.items) |*item| item.deinit(self.allocator);
                    items.deinit(self.allocator);
                }
                try self.next();
                while (self.event.kind != 9) {
                    if (self.event.kind != 5) return error.UnsupportedFrontmatter;
                    if (depth + 1 > max_depth) return error.FrontmatterTooLarge;
                    var item = try scalar(self.allocator, self.bytes(), self.event.plain != 0);
                    items.append(self.allocator, item) catch |err| {
                        item.deinit(self.allocator);
                        return err;
                    };
                    try self.next();
                }
                const owned = try items.toOwnedSlice(self.allocator);
                self.result.fields.items[index].value = .{ .array = owned };
                try self.next();
            },
            else => return error.UnsupportedFrontmatter,
        }
    }
};

/// 原文を解析する。返却したmetadataはdeinitする。原文自体は保持・変更しない。
pub fn parse(allocator: Allocator, text: []const u8) Error!Metadata {
    const split = try header(text);
    var result: Metadata = .{ .body_start = split.body_start, .has_frontmatter = split.present };
    errdefer result.deinit(allocator);
    if (!split.present) return result;
    const handle = zigurui_yaml_new(split.payload.ptr, split.payload.len) orelse return error.OutOfMemory;
    defer zigurui_yaml_delete(handle);
    var parser: Parser = .{ .allocator = allocator, .handle = handle, .result = &result };
    try parser.next();
    if (parser.event.kind != 1) return error.InvalidFrontmatter;
    try parser.next();
    if (parser.event.kind == 2) return result;
    if (parser.event.kind != 3) return error.InvalidFrontmatter;
    try parser.next();
    if (parser.event.kind != 6) return error.UnsupportedFrontmatter;
    try parser.map("", 1);
    if (parser.event.kind != 4) return error.InvalidFrontmatter;
    try parser.next();
    if (parser.event.kind != 2) return error.UnsupportedFrontmatter;
    return result;
}

pub const SelectorKind = enum { metadata, file_name, file_path };
const builtin_selectors = [_][]const u8{ "file:/name", "file:/path" };

/// selectorの名前空間と構文を検証する。metadataのキーとfileの固定項目は混ざらない。
pub fn selectorKind(selector: []const u8) Error!SelectorKind {
    if (std.mem.eql(u8, selector, builtin_selectors[0])) return .file_name;
    if (std.mem.eql(u8, selector, builtin_selectors[1])) return .file_path;
    if (selector.len == 0 or selector[0] != '/' or selector.len > max_pointer_bytes or !std.unicode.utf8ValidateSlice(selector)) return error.InvalidFilter;
    var i: usize = 0;
    while (i < selector.len) : (i += 1) {
        if (selector[i] == '~') {
            i += 1;
            if (i == selector.len or (selector[i] != '0' and selector[i] != '1')) return error.InvalidFilter;
        }
    }
    return .metadata;
}

/// selectorと期待値を所有する。metadata・組み込み値のどちらも正規化せず比較する。
pub const Filter = struct {
    pointer: []u8,
    expected: Scalar,
    kind: SelectorKind,

    pub fn init(allocator: Allocator, pointer: []const u8, json: []const u8) Error!Filter {
        const kind = try selectorKind(pointer);
        if (json.len > max_header_bytes) return error.InvalidFilter;
        const trimmed = std.mem.trim(u8, json, " \t\n\r");
        if (trimmed.len == 0 or trimmed[0] == '[' or trimmed[0] == '{') return error.InvalidFilter;
        const parsed = std.json.parseFromSlice(std.json.Value, allocator, json, .{ .allocate = .alloc_always, .parse_numbers = false }) catch |err| {
            if (err == error.OutOfMemory) return error.OutOfMemory;
            return error.InvalidFilter;
        };
        defer parsed.deinit();
        var expected: Scalar = switch (parsed.value) {
            .null => .null,
            .bool => |v| .{ .boolean = v },
            .number_string => |v| try number(v, numberKind(v) orelse return error.InvalidFilter),
            .string => |v| .{ .string = try allocator.dupe(u8, v) },
            else => return error.InvalidFilter,
        };
        errdefer expected.deinit(allocator);
        return .{ .pointer = try allocator.dupe(u8, pointer), .expected = expected, .kind = kind };
    }

    pub fn deinit(self: *Filter, allocator: Allocator) void {
        allocator.free(self.pointer);
        self.expected.deinit(allocator);
    }
};

/// 配列だけを所有する。documents内のパス・本文は入力文書を借用し、先に解放しない。
pub const Projection = struct {
    documents: []search.Document,
    body_starts: []usize,
    eligible: []bool,

    /// diagnosticは開始時にclearし、header解析失敗時だけ文書添字を設定する。
    pub fn init(allocator: Allocator, documents: []const search.Document, filters: []const Filter, parse_frontmatter: bool, diagnostic: ?*?Diagnostic) Error!Projection {
        if (diagnostic) |out| out.* = null;
        if (filters.len > max_filters) return error.InvalidFilter;
        if (!parse_frontmatter) {
            for (filters) |filter| if (filter.kind == .metadata) return error.InvalidFilter;
        }
        const projected = try allocator.alloc(search.Document, documents.len);
        errdefer allocator.free(projected);
        const starts = try allocator.alloc(usize, documents.len);
        errdefer allocator.free(starts);
        const eligible = try allocator.alloc(bool, documents.len);
        errdefer allocator.free(eligible);
        for (documents, 0..) |document, i| {
            // 明示時はfile条件に合わない文書も検証し、非明示時はYAMLを読まない。
            var metadata = if (parse_frontmatter) parse(allocator, document.text) catch |err| {
                recordDiagnostic(diagnostic, i, .frontmatter, err);
                return err;
            } else Metadata{};
            defer metadata.deinit(allocator);
            starts[i] = metadata.body_start;
            eligible[i] = metadata.matches(filters, document.path);
            projected[i] = .{ .path = document.path, .text = document.text[metadata.body_start..] };
        }
        return .{ .documents = projected, .body_starts = starts, .eligible = eligible };
    }

    pub fn deinit(self: *Projection, allocator: Allocator) void {
        allocator.free(self.documents);
        allocator.free(self.body_starts);
        allocator.free(self.eligible);
    }

    /// 本文で得た両端のUTF-8バイト位置を原文の位置へ戻す。
    pub fn restore(self: *const Projection, matches: []search.Match) void {
        for (matches) |*match| {
            match.offset += self.body_starts[match.doc_id];
            match.end += self.body_starts[match.doc_id];
        }
    }
};

const type_names = [_][]const u8{ "array", "boolean", "float", "integer", "null", "object", "string" };
const Type = enum(u3) { array, boolean, float, integer, null, object, string };
fn scalarType(value: Scalar) Type {
    return switch (value) {
        .null => .null,
        .boolean => .boolean,
        .integer => .integer,
        .float => .float,
        .string => .string,
    };
}
fn typeBit(value: Type) u8 {
    return @as(u8, 1) << @backingInt(value);
}
const Stat = struct { pointer: []u8, types: u8 = 0, present: usize = 0, null_count: usize = 0, element_types: u8 = 0 };
const FieldView = struct { pointer: []const u8, types: []const []const u8, present: usize, missing: usize, null_count: usize, element_types: []const []const u8 };
fn names(allocator: Allocator, bits: u8) ![][]const u8 {
    const result = try allocator.alloc([]const u8, @popCount(bits));
    var next: usize = 0;
    for (type_names, 0..) |name, i| {
        if (bits & (@as(u8, 1) << @as(u3, @intCast(i))) != 0) {
            result[next] = name;
            next += 1;
        }
    }
    return result;
}

/// 全文書を検証してスキーマJSONを返す。返却bytesはallocator.freeする。
/// presentは文書数で数え、missingはfrontmatterなしを含む全文書数との差。
/// diagnosticは開始時にclearし、解析失敗またはunion上限超過時に文書添字を設定する。
pub fn schemaJson(allocator: Allocator, documents: []const search.Document, diagnostic: ?*?Diagnostic) Error![]u8 {
    if (diagnostic) |out| out.* = null;
    var fields: std.ArrayList(Stat) = .empty;
    defer {
        for (fields.items) |field| allocator.free(field.pointer);
        fields.deinit(allocator);
    }
    var lookup: std.StringHashMap(usize) = .init(allocator);
    defer lookup.deinit();
    var frontmatter_count: usize = 0;
    for (documents, 0..) |document, document_index| {
        var metadata = parse(allocator, document.text) catch |err| {
            recordDiagnostic(diagnostic, document_index, .frontmatter, err);
            return err;
        };
        defer metadata.deinit(allocator);
        if (metadata.has_frontmatter) frontmatter_count += 1;
        for (metadata.fields.items) |field| {
            const index = lookup.get(field.pointer) orelse blk: {
                if (fields.items.len == max_schema_paths) {
                    recordDiagnostic(diagnostic, document_index, .schema_aggregation, error.FrontmatterTooLarge);
                    return error.FrontmatterTooLarge;
                }
                const pointer = try allocator.dupe(u8, field.pointer);
                fields.append(allocator, .{ .pointer = pointer }) catch |err| {
                    allocator.free(pointer);
                    return err;
                };
                const added = fields.items.len - 1;
                try lookup.put(pointer, added);
                break :blk added;
            };
            const stat = &fields.items[index];
            stat.present += 1;
            switch (field.value) {
                .scalar => |value| {
                    stat.types |= typeBit(scalarType(value));
                    if (value == .null) stat.null_count += 1;
                },
                .object => stat.types |= typeBit(.object),
                .array => |items| {
                    stat.types |= typeBit(.array);
                    for (items) |item| stat.element_types |= typeBit(scalarType(item));
                },
            }
        }
    }
    std.mem.sort(Stat, fields.items, {}, struct {
        fn less(_: void, a: Stat, b: Stat) bool {
            return std.mem.lessThan(u8, a.pointer, b.pointer);
        }
    }.less);
    var views: std.ArrayList(FieldView) = .empty;
    defer {
        for (views.items) |view| {
            allocator.free(view.types);
            allocator.free(view.element_types);
        }
        views.deinit(allocator);
    }
    for (fields.items) |field| {
        const types = try names(allocator, field.types);
        errdefer allocator.free(types);
        const element_types = try names(allocator, field.element_types);
        errdefer allocator.free(element_types);
        try views.append(allocator, .{ .pointer = field.pointer, .types = types, .present = field.present, .missing = documents.len - field.present, .null_count = field.null_count, .element_types = element_types });
    }
    const BuiltinView = struct {
        selector: []const u8,
        types: []const []const u8 = &.{"string"},
        present: usize,
        missing: usize = 0,
        null_count: usize = 0,
        element_types: []const []const u8 = &.{},
    };
    const builtin_fields = [_]BuiltinView{
        .{ .selector = builtin_selectors[0], .present = documents.len },
        .{ .selector = builtin_selectors[1], .present = documents.len },
    };
    return std.json.Stringify.valueAlloc(allocator, .{ .document_count = documents.len, .frontmatter_count = frontmatter_count, .fields = views.items, .builtin_fields = &builtin_fields }, .{});
}

const testing = std.testing;

test "frontmatterは先頭の完全なLFとCRLF区切りだけを認める" {
    const allocator = testing.allocator;
    for ([_][]const u8{ "本文\n---\na: 1\n---\n", "--- \na: 1\n---\n", "---\r\r\na: 1\n---\n", "---\r" }) |text| {
        var metadata = try parse(allocator, text);
        defer metadata.deinit(allocator);
        try testing.expect(!metadata.has_frontmatter);
        try testing.expectEqual(@as(usize, 0), metadata.body_start);
    }
    const text = "\xef\xbb\xbf---\r\na: 1\r\n...\r\n本文";
    var metadata = try parse(allocator, text);
    defer metadata.deinit(allocator);
    try testing.expect(metadata.has_frontmatter);
    try testing.expectEqualStrings("本文", text[metadata.body_start..]);
    for ([_][]const u8{ "---\n---\n", "---\n# comment\n...", "---\r\n{}\r\n---\r\n" }) |empty| {
        var result = try parse(allocator, empty);
        defer result.deinit(allocator);
        try testing.expect(result.has_frontmatter and result.fields.items.len == 0);
    }
    try testing.expectError(error.InvalidFrontmatter, parse(allocator, "---\na: 1\n"));
    try testing.expectError(error.InvalidFrontmatter, parse(allocator, "---\na: 1\n---\r\r\n"));
}

test "scalar型はJSON数値と小文字予約語だけを解釈し引用と日付を文字列にする" {
    const allocator = testing.allocator;
    var metadata = try parse(allocator, "---\nempty:\nnil: null\ntilde: ~\ny: true\nn: false\ni: -1\nf: 1.0\ne: 1e0\nquoted: '1'\nzero: 01\nyes: yes\non: on\ncapital: True\ndate: 2026-11-14\nblock: |\n  hi\n---\nbody");
    defer metadata.deinit(allocator);
    for ([_][]const u8{ "/empty", "/nil", "/tilde" }) |key| try testing.expect(metadata.find(key).?.scalar == .null);
    try testing.expect(metadata.find("/y").?.scalar.boolean);
    try testing.expect(!metadata.find("/n").?.scalar.boolean);
    try testing.expectEqual(@as(i64, -1), metadata.find("/i").?.scalar.integer);
    try testing.expectEqual(@as(f64, 1), metadata.find("/f").?.scalar.float);
    try testing.expectEqual(@as(f64, 1), metadata.find("/e").?.scalar.float);
    for ([_][]const u8{ "/quoted", "/zero", "/yes", "/on", "/capital", "/date", "/block" }) |key| try testing.expect(metadata.find(key).?.scalar == .string);
    try testing.expectEqualStrings("hi\n", metadata.find("/block").?.scalar.string);
    for ([_][]const u8{ "9223372036854775808", "-9223372036854775809", "1e309", "1e-999" }) |bad| {
        const text = try std.fmt.allocPrint(allocator, "---\nx: {s}\n---\n", .{bad});
        defer allocator.free(text);
        try testing.expectError(error.FrontmatterNumberOutOfRange, parse(allocator, text));
        try testing.expectError(error.FrontmatterNumberOutOfRange, Filter.init(allocator, "/x", bad));
    }
}

test "JSON pointerと型付きfilterはmissingとnullとintとfloatを区別する" {
    const allocator = testing.allocator;
    var metadata = try parse(allocator, "---\n'a/b':\n  '~=': ok\ni: 1\nf: 1.0\nn: null\ntags: [ready, 1, false]\n---\nbody");
    defer metadata.deinit(allocator);
    const cases = [_]struct { pointer: []const u8, json: []const u8, expected: bool }{
        .{ .pointer = "/a~1b/~0=", .json = "\"ok\"", .expected = true },
        .{ .pointer = "/i", .json = "1", .expected = true },
        .{ .pointer = "/i", .json = "1.0", .expected = false },
        .{ .pointer = "/f", .json = "1.0", .expected = true },
        .{ .pointer = "/f", .json = "1", .expected = false },
        .{ .pointer = "/i", .json = "\"1\"", .expected = false },
        .{ .pointer = "/n", .json = "null", .expected = true },
        .{ .pointer = "/missing", .json = "null", .expected = false },
        .{ .pointer = "/a~1b", .json = "\"ok\"", .expected = false },
        .{ .pointer = "/tags", .json = "\"ready\"", .expected = true },
        .{ .pointer = "/tags", .json = "1", .expected = true },
        .{ .pointer = "/tags", .json = "1.0", .expected = false },
        .{ .pointer = "/tags", .json = "false", .expected = true },
    };
    for (cases) |case| {
        var filter = try Filter.init(allocator, case.pointer, case.json);
        defer filter.deinit(allocator);
        try testing.expectEqual(case.expected, metadata.matches(&.{filter}, ""));
    }
    for ([_][]const u8{ "", "x", "/a~", "/a~2" }) |pointer| try testing.expectError(error.InvalidFilter, Filter.init(allocator, pointer, "1"));
    for ([_][]const u8{ "{}", "[]", "yes", "01", "\"x\" \"y\"" }) |json| try testing.expectError(error.InvalidFilter, Filter.init(allocator, "/x", json));
}

test "スキーマは混在型と文書単位presentと全doc分母missingを安定順で返す" {
    const allocator = testing.allocator;
    const documents = [_]search.Document{
        .{ .path = "a", .text = "---\nstatus: ready\nx: null\ntags: []\nmeta:\n  flag: true\n---\nbody" },
        .{ .path = "b", .text = "---\nstatus: 1\nx: 'null'\ntags: [a, 1, false]\n---\nbody" },
        .{ .path = "c", .text = "no frontmatter" },
    };
    const json = try schemaJson(allocator, &documents, null);
    defer allocator.free(json);
    try testing.expectEqualStrings("{\"document_count\":3,\"frontmatter_count\":2,\"fields\":[" ++
        "{\"pointer\":\"/meta\",\"types\":[\"object\"],\"present\":1,\"missing\":2,\"null_count\":0,\"element_types\":[]}," ++
        "{\"pointer\":\"/meta/flag\",\"types\":[\"boolean\"],\"present\":1,\"missing\":2,\"null_count\":0,\"element_types\":[]}," ++
        "{\"pointer\":\"/status\",\"types\":[\"integer\",\"string\"],\"present\":2,\"missing\":1,\"null_count\":0,\"element_types\":[]}," ++
        "{\"pointer\":\"/tags\",\"types\":[\"array\"],\"present\":2,\"missing\":1,\"null_count\":0,\"element_types\":[\"boolean\",\"integer\",\"string\"]}," ++
        "{\"pointer\":\"/x\",\"types\":[\"null\",\"string\"],\"present\":2,\"missing\":1,\"null_count\":1,\"element_types\":[]}],\"builtin_fields\":[" ++
        "{\"selector\":\"file:/name\",\"types\":[\"string\"],\"present\":3,\"missing\":0,\"null_count\":0,\"element_types\":[]}," ++
        "{\"selector\":\"file:/path\",\"types\":[\"string\"],\"present\":3,\"missing\":0,\"null_count\":0,\"element_types\":[]}]}", json);
}

test "aliasとtagとmergeと複合配列と重複キーを明示的に拒否する" {
    const allocator = testing.allocator;
    for ([_][]const u8{ "x: &a 1\ny: *a", "x: *a", "x: !!str 1", "<<: x", "'<<': x", "x: [[1]]", "x: [{a: 1}]", "[a, b]", "null", "? [a,b]\n: x" }) |payload| {
        const text = try std.fmt.allocPrint(allocator, "---\n{s}\n---\n", .{payload});
        defer allocator.free(text);
        try testing.expectError(error.UnsupportedFrontmatter, parse(allocator, text));
    }
    for ([_][]const u8{ "x: 1\nx: 2", "'x': 1\nx: 2", "nested: {x: 1, x: 2}", "x: [1, 2" }) |payload| {
        const text = try std.fmt.allocPrint(allocator, "---\n{s}\n---\n", .{payload});
        defer allocator.free(text);
        try testing.expectError(error.InvalidFrontmatter, parse(allocator, text));
    }
}

fn nestedFixture(allocator: Allocator, maps: usize) ![]u8 {
    var output: std.ArrayList(u8) = .empty;
    errdefer output.deinit(allocator);
    try output.appendSlice(allocator, "---\n");
    for (0..maps) |_| try output.appendSlice(allocator, "{k: ");
    try output.appendSlice(allocator, "true");
    for (0..maps) |_| try output.append(allocator, '}');
    try output.appendSlice(allocator, "\n---\n");
    return output.toOwnedSlice(allocator);
}

test "frontmatterの深さはrootを1として値ノード16までを許す" {
    const allocator = testing.allocator;
    const exact = try nestedFixture(allocator, 15);
    defer allocator.free(exact);
    var metadata = try parse(allocator, exact);
    defer metadata.deinit(allocator);
    try testing.expectEqual(@as(usize, 15), metadata.fields.items.len);
    for ([_]usize{ 16, 64 }) |maps| {
        const text = try nestedFixture(allocator, maps);
        defer allocator.free(text);
        try testing.expectError(error.FrontmatterTooLarge, parse(allocator, text));
    }
}

test "header byteとpointer byteの上限ちょうどを許し超過を拒否する" {
    const allocator = testing.allocator;
    var text: std.ArrayList(u8) = .empty;
    defer text.deinit(allocator);
    try text.appendSlice(allocator, "---\n#");
    try text.appendNTimes(allocator, ' ', max_header_bytes - 2);
    try text.appendSlice(allocator, "\n---\n");
    var exact = try parse(allocator, text.items);
    defer exact.deinit(allocator);
    try text.insert(allocator, 5, ' ');
    try testing.expectError(error.FrontmatterTooLarge, parse(allocator, text.items));
    text.clearRetainingCapacity();
    try text.appendSlice(allocator, "---\n? \"");
    try text.appendNTimes(allocator, 'a', max_pointer_bytes - 1);
    try text.appendSlice(allocator, "\"\n: true\n---\n");
    var pointer = try parse(allocator, text.items);
    defer pointer.deinit(allocator);
    try testing.expectEqual(max_pointer_bytes, pointer.fields.items[0].pointer.len);
    try text.insert(allocator, 7, 'a');
    try testing.expectError(error.FrontmatterTooLarge, parse(allocator, text.items));
}

fn nodeFixture(allocator: Allocator, extra_item: bool) ![]u8 {
    var output: std.ArrayList(u8) = .empty;
    errdefer output.deinit(allocator);
    try output.appendSlice(allocator, "---\n");
    for (0..2046) |i| {
        const line = try std.fmt.allocPrint(allocator, "k{d}: 0\n", .{i});
        defer allocator.free(line);
        try output.appendSlice(allocator, line);
    }
    try output.appendSlice(allocator, if (extra_item) "last: [0, 0]\n---\n" else "last: [0]\n---\n");
    return output.toOwnedSlice(allocator);
}

test "nodes上限4096はmapとkeyとvalueと配列要素を含む" {
    const allocator = testing.allocator;
    const exact = try nodeFixture(allocator, false);
    defer allocator.free(exact);
    var metadata = try parse(allocator, exact);
    defer metadata.deinit(allocator);
    try testing.expectEqual(@as(usize, 2047), metadata.fields.items.len);
    const over = try nodeFixture(allocator, true);
    defer allocator.free(over);
    try testing.expectError(error.FrontmatterTooLarge, parse(allocator, over));
}

fn allocationScenario(allocator: Allocator) !void {
    const documents = [_]search.Document{
        .{ .path = "a", .text = "---\ntags: [a, 1, null]\nmeta: {flag: true}\n---\n前㌔後" },
        .{ .path = "b", .text = "---\nmeta: null\n---\nガ" },
    };
    var filter = try Filter.init(allocator, "/tags", "\"a\"");
    defer filter.deinit(allocator);
    var projection = try Projection.init(allocator, &documents, &.{filter}, true, null);
    defer projection.deinit(allocator);
    try testing.expectEqualSlices(bool, &.{ true, false }, projection.eligible);
    const json = try schemaJson(allocator, &documents, null);
    defer allocator.free(json);
}

test "metadataとfilterとschemaとprojectionは確保失敗でも所有メモリを残さない" {
    try testing.checkAllAllocationFailures(testing.allocator, allocationScenario, .{});
}

fn schemaFixture(allocator: Allocator, begin: usize, count: usize) ![]u8 {
    var output: std.ArrayList(u8) = .empty;
    errdefer output.deinit(allocator);
    try output.appendSlice(allocator, "---\n");
    for (begin..begin + count) |i| {
        const line = try std.fmt.allocPrint(allocator, "k{d}: true\n", .{i});
        defer allocator.free(line);
        try output.appendSlice(allocator, line);
    }
    try output.appendSlice(allocator, "---\nbody");
    return output.toOwnedSlice(allocator);
}

test "schemaのunique path上限は全docを合わせて4096とする" {
    const allocator = testing.allocator;
    const first = try schemaFixture(allocator, 0, 2047);
    defer allocator.free(first);
    const second = try schemaFixture(allocator, 2047, 2047);
    defer allocator.free(second);
    const exact = try schemaFixture(allocator, 4094, 2);
    defer allocator.free(exact);
    const over = try schemaFixture(allocator, 4094, 3);
    defer allocator.free(over);
    var docs = [_]search.Document{ .{ .path = "a", .text = first }, .{ .path = "b", .text = second }, .{ .path = "c", .text = exact } };
    const json = try schemaJson(allocator, &docs, null);
    defer allocator.free(json);
    docs[2].text = over;
    var diagnostic: ?Diagnostic = null;
    try testing.expectError(error.FrontmatterTooLarge, schemaJson(allocator, &docs, &diagnostic));
    try testing.expectEqual(@as(usize, 2), diagnostic.?.document_index);
    try testing.expectEqual(.schema_aggregation, diagnostic.?.context);
    try testing.expectEqual(error.FrontmatterTooLarge, diagnostic.?.reason);
    // 検索は文書ごとのmetadataを解放し、全体のpath unionを保持しない。
    var projection = try Projection.init(allocator, &docs, &.{}, true, null);
    defer projection.deinit(allocator);
    try testing.expectEqualSlices(bool, &.{ true, true, true }, projection.eligible);
}

test "JSON filterのbyte上限をOS引数制限なしで検証する" {
    const allocator = testing.allocator;
    const json = try allocator.alloc(u8, max_header_bytes + 1);
    defer allocator.free(json);
    @memset(json, 'a');
    json[0] = '"';
    json[max_header_bytes - 1] = '"';
    var exact = try Filter.init(allocator, "/x", json[0..max_header_bytes]);
    defer exact.deinit(allocator);
    try testing.expectEqual(@as(usize, max_header_bytes - 2), exact.expected.string.len);
    json[max_header_bytes - 1] = 'a';
    json[max_header_bytes] = '"';
    try testing.expectError(error.InvalidFilter, Filter.init(allocator, "/x", json));
    const pointer = try allocator.alloc(u8, max_pointer_bytes + 1);
    defer allocator.free(pointer);
    @memset(pointer, 'x');
    pointer[0] = '/';
    var exact_pointer = try Filter.init(allocator, pointer[0..max_pointer_bytes], "0.0e-999");
    defer exact_pointer.deinit(allocator);
    try testing.expectEqual(@as(f64, 0), exact_pointer.expected.float);
    try testing.expectError(error.InvalidFilter, Filter.init(allocator, pointer, "null"));
}

test "組み込みnameは同名全件を選びpathは保存文字列をそのまま比較する" {
    const allocator = testing.allocator;
    const documents = [_]search.Document{
        .{ .path = "/kb/a/会議.MD", .text = "---\nbroken YAML" },
        .{ .path = "/kb/b/会議.MD", .text = "body" },
        .{ .path = "/kb/会議.md", .text = "body" },
        .{ .path = "relative/会議.MD", .text = "body" },
    };
    var name = try Filter.init(allocator, "file:/name", "\"会議.MD\"");
    defer name.deinit(allocator);
    var projected = try Projection.init(allocator, &documents, &.{name}, false, null);
    defer projected.deinit(allocator);
    try testing.expectEqualSlices(bool, &.{ true, true, false, true }, projected.eligible);
    try testing.expectEqualSlices(usize, &.{ 0, 0, 0, 0 }, projected.body_starts);
    try testing.expectEqual(documents[0].text.ptr, projected.documents[0].text.ptr);
    var path = try Filter.init(allocator, "file:/path", "\"relative/会議.MD\"");
    defer path.deinit(allocator);
    const empty: Metadata = .{};
    try testing.expect(empty.matches(&.{path}, documents[3].path));
    try testing.expect(!empty.matches(&.{path}, "/relative/会議.MD"));
    try testing.expect(!empty.matches(&.{name}, "/kb/会議.md"));
    var width = try Filter.init(allocator, "file:/name", "\"Ａ.md\"");
    defer width.deinit(allocator);
    try testing.expect(empty.matches(&.{width}, "/kb/Ａ.md"));
    try testing.expect(!empty.matches(&.{width}, "/kb/A.md"));
}

test "file selectorの非string条件は型不一致で未知selectorは拒否する" {
    const allocator = testing.allocator;
    const empty: Metadata = .{};
    for ([_][]const u8{ "null", "true", "1", "1.0" }) |json| {
        var filter = try Filter.init(allocator, "file:/name", json);
        defer filter.deinit(allocator);
        try testing.expect(!empty.matches(&.{filter}, "/kb/1"));
    }
    for ([_][]const u8{ "file:/size", "file:/Name", "file:/name/x", "file:/", "FILE:/name" }) |selector| {
        try testing.expectError(error.InvalidFilter, Filter.init(allocator, selector, "\"x\""));
    }
}

test "YAMLの任意キーとfile selectorは独立し混在条件をANDで扱う" {
    const allocator = testing.allocator;
    const text = "---\nfile: {name: nested.md}\n'file:/name': yaml.md\nname: other.md\n---\nbody";
    const documents = [_]search.Document{.{ .path = "/kb/actual.md", .text = text }};
    var builtin = try Filter.init(allocator, "file:/name", "\"actual.md\"");
    defer builtin.deinit(allocator);
    var nested = try Filter.init(allocator, "/file/name", "\"nested.md\"");
    defer nested.deinit(allocator);
    var escaped = try Filter.init(allocator, "/file:~1name", "\"yaml.md\"");
    defer escaped.deinit(allocator);
    var ordinary = try Filter.init(allocator, "/name", "\"other.md\"");
    defer ordinary.deinit(allocator);
    var projected = try Projection.init(allocator, &documents, &.{ builtin, nested, escaped, ordinary }, true, null);
    defer projected.deinit(allocator);
    try testing.expect(projected.eligible[0]);
    try testing.expectEqualStrings("body", projected.documents[0].text);
    try testing.expectError(error.InvalidFilter, Projection.init(allocator, &documents, &.{ builtin, nested }, false, null));
    const invalid = [_]search.Document{.{ .path = "/kb/unselected.md", .text = "---\nmissing end" }};
    try testing.expectError(error.InvalidFrontmatter, Projection.init(allocator, &invalid, &.{builtin}, true, null));
}

test "空schemaも組み込みstring定義を別namespaceに返す" {
    const allocator = testing.allocator;
    const json = try schemaJson(allocator, &.{}, null);
    defer allocator.free(json);
    try testing.expectEqualStrings("{\"document_count\":0,\"frontmatter_count\":0,\"fields\":[],\"builtin_fields\":[" ++
        "{\"selector\":\"file:/name\",\"types\":[\"string\"],\"present\":0,\"missing\":0,\"null_count\":0,\"element_types\":[]}," ++
        "{\"selector\":\"file:/path\",\"types\":[\"string\"],\"present\":0,\"missing\":0,\"null_count\":0,\"element_types\":[]}]}", json);
}

fn builtinAllocationScenario(allocator: Allocator) !void {
    const documents = [_]search.Document{.{ .path = "/kb/a.md", .text = "---\nstate: ready\n---\nbody" }};
    var builtin = try Filter.init(allocator, "file:/name", "\"a.md\"");
    defer builtin.deinit(allocator);
    var metadata = try Filter.init(allocator, "/state", "\"ready\"");
    defer metadata.deinit(allocator);
    var raw = try Projection.init(allocator, &documents, &.{builtin}, false, null);
    defer raw.deinit(allocator);
    var body = try Projection.init(allocator, &documents, &.{ builtin, metadata }, true, null);
    defer body.deinit(allocator);
    try testing.expect(raw.eligible[0] and body.eligible[0]);
    const json = try schemaJson(allocator, &.{}, null);
    defer allocator.free(json);
}

test "builtin filterとrawとbodyの投影は確保失敗でも所有メモリを残さない" {
    try testing.checkAllAllocationFailures(testing.allocator, builtinAllocationScenario, .{});
}

test "診断は失敗した文書を指し次の処理でclearされる" {
    const documents = [_]search.Document{
        .{ .path = "good.md", .text = "---\nx: ready\n---\nbody" },
        .{ .path = "bad.md", .text = "---\nx: [broken\n---\nbody" },
    };
    var diagnostic: ?Diagnostic = null;
    var filter = try Filter.init(testing.allocator, "file:/name", "\"absent.md\"");
    defer filter.deinit(testing.allocator);
    try testing.expectError(error.InvalidFrontmatter, Projection.init(testing.allocator, &documents, &.{filter}, true, &diagnostic));
    try testing.expectEqual(@as(usize, 1), diagnostic.?.document_index);
    try testing.expectEqual(.frontmatter, diagnostic.?.context);
    try testing.expectEqual(error.InvalidFrontmatter, diagnostic.?.reason);
    var raw = try Projection.init(testing.allocator, &documents, &.{filter}, false, &diagnostic);
    defer raw.deinit(testing.allocator);
    try testing.expect(diagnostic == null);
    try testing.expectError(error.InvalidFrontmatter, schemaJson(testing.allocator, &documents, &diagnostic));
    try testing.expectEqual(@as(usize, 1), diagnostic.?.document_index);
    try testing.expectEqual(.frontmatter, diagnostic.?.context);
    const json = try schemaJson(testing.allocator, &.{}, &diagnostic);
    defer testing.allocator.free(json);
    try testing.expect(diagnostic == null);
}
