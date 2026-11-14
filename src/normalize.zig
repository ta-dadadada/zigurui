//! 検索用のNFKC→default full case folding→NFKCと、原文バイト区間の対応を担う。
//! Unicodeデータ・分解・合成対は固定版utf8procを使い、順序制御と由来の追跡を行う。
const std = @import("std");
const profile = @import("profile.zig");
extern fn zigurui_decompose(cp: i32, out: ?[*]i32, capacity: isize, fold: bool) isize;
extern fn zigurui_combining_class(cp: i32) u8;
extern fn zigurui_compose_pair(first: i32, second: i32) i32;
extern fn zigurui_reference_map(input: [*]const u8, length: isize, out: *?[*]u8, fold: bool) isize;
extern fn utf8proc_free(ptr: ?[*]u8) void;
const Allocator = std.mem.Allocator;

pub const semantic_version: u32 = 1;
pub const unicode_version: u32 = 0x00120000;
/// 非ASCII入力の各中間表現の最大コードポイント数。超過は文書を省略せず失敗する。
pub const max_codepoints: usize = 4 * 1024 * 1024;

/// 原文UTF-8の半開バイト区間。
pub const Span = struct { start: usize, end: usize };
const Point = struct { cp: u21, ccc: u8, start: u32, end: u32 };

/// textとpointsを所有する。ASCII入力では対応が恒等なのでpointsは空。
pub const Result = struct {
    text: []u8,
    points: []Point,
    allocator: Allocator,

    pub fn deinit(self: *Result) void {
        self.allocator.free(self.text);
        self.allocator.free(self.points);
        self.* = undefined;
    }

    /// 正規化UTF-8の非空一致区間を、全由来を覆う原文区間へ戻す。
    /// start/endはコードポイント境界。並べ替えのため原文位置は単調とは限らない。
    pub fn originalSpan(self: *const Result, start: usize, end: usize) Span {
        std.debug.assert(start < end and end <= self.text.len);
        if (self.points.len == 0) return .{ .start = start, .end = end };
        var span: Span = .{ .start = std.math.maxInt(usize), .end = 0 };
        var offset: usize = 0;
        for (self.points) |point| {
            profile.add(.span_points_visited, 1);
            const next = offset + (std.unicode.utf8CodepointSequenceLength(point.cp) catch unreachable);
            if (offset >= end) break;
            if (next > start) {
                span.start = @min(span.start, point.start);
                span.end = @max(span.end, point.end);
            }
            offset = next;
        }
        return span;
    }
};

/// 有効なUTF-8を検索表現へ変換する。返却値は呼び出し側がdeinitする。
/// 不正UTF-8、サイズ上限、確保失敗を返す。不可視文字や改行は除去しない。
pub fn transform(allocator: Allocator, text: []const u8) !Result {
    if (!std.unicode.utf8ValidateSlice(text)) return error.InvalidUtf8;
    if (text.len > std.math.maxInt(u32)) return error.NormalizationTooLarge;
    if (isAscii(text)) {
        const out = try allocator.dupe(u8, text);
        for (out) |*byte| byte.* = std.ascii.toLower(byte.*);
        return .{ .text = out, .points = &.{}, .allocator = allocator };
    }
    var input: std.ArrayList(Point) = .empty;
    defer input.deinit(allocator);
    var iter = std.unicode.Utf8View.initUnchecked(text).iterator();
    while (iter.i < text.len) {
        const start = iter.i;
        const cp = iter.nextCodepoint().?;
        try appendPoint(allocator, &input, .{ .cp = cp, .ccc = 0, .start = @intCast(start), .end = @intCast(iter.i) });
    }
    var first = try mapped(allocator, input.items, false, true);
    defer first.deinit(allocator);
    // 中間配列を早く解放し、同時に保持する表現を二つに抑える。
    input.clearAndFree(allocator);
    var folded = try mapped(allocator, first.items, true, false);
    defer folded.deinit(allocator);
    first.clearAndFree(allocator);
    var final = try mapped(allocator, folded.items, false, true);
    defer final.deinit(allocator);
    folded.clearAndFree(allocator);
    var bytes: std.ArrayList(u8) = .empty;
    defer bytes.deinit(allocator);
    for (final.items) |point| {
        var encoded: [4]u8 = undefined;
        const len = std.unicode.utf8Encode(point.cp, &encoded) catch unreachable;
        try bytes.appendSlice(allocator, encoded[0..len]);
    }
    const out = try bytes.toOwnedSlice(allocator);
    errdefer allocator.free(out);
    return .{ .text = out, .points = try final.toOwnedSlice(allocator), .allocator = allocator };
}

fn appendPoint(allocator: Allocator, points: *std.ArrayList(Point), point: Point) !void {
    if (points.items.len == max_codepoints) return error.NormalizationTooLarge;
    try points.append(allocator, point);
}

fn mapped(allocator: Allocator, input: []const Point, fold: bool, compose: bool) !std.ArrayList(Point) {
    var output: std.ArrayList(Point) = .empty;
    errdefer output.deinit(allocator);
    var scratch: std.ArrayList(i32) = .empty;
    defer scratch.deinit(allocator);
    var small: [8]i32 = undefined;
    for (input) |point| {
        const needed = zigurui_decompose(point.cp, &small, small.len, fold);
        if (needed < 0) return error.InvalidUnicodeData;
        const count: usize = @intCast(needed);
        if (count > max_codepoints - output.items.len) return error.NormalizationTooLarge;
        const decomposed = if (count <= small.len) small[0..count] else blk: {
            // 容量不足時の部分出力は使わず、必要量を確保して全体を書き直す。
            try scratch.resize(allocator, count);
            if (zigurui_decompose(point.cp, scratch.items.ptr, needed, fold) != needed) return error.InvalidUnicodeData;
            break :blk scratch.items;
        };
        for (decomposed) |cp| {
            try output.append(allocator, .{ .cp = @intCast(cp), .ccc = zigurui_combining_class(cp), .start = point.start, .end = point.end });
        }
    }
    if (compose) {
        // CCC=0の前後を跨がず、同じCCCの元の順序を保つ。長い結合列もO(n log n)。
        var begin: usize = 0;
        for (output.items, 0..) |point, i| {
            if (point.ccc == 0) {
                std.mem.sort(Point, output.items[begin..i], {}, lessCcc);
                begin = i + 1;
            }
        }
        std.mem.sort(Point, output.items[begin..], {}, lessCcc);
        var starter: ?usize = null;
        var last_ccc: u8 = 0;
        var written: usize = 0;
        for (output.items) |point| {
            if (starter) |s| {
                if (last_ccc == 0 or last_ccc < point.ccc) {
                    const combined = zigurui_compose_pair(output.items[s].cp, point.cp);
                    if (combined >= 0) {
                        output.items[s].cp = @intCast(combined);
                        output.items[s].start = @min(output.items[s].start, point.start);
                        output.items[s].end = @max(output.items[s].end, point.end);
                        // 合成で消えた文字は後続文字の合成をblockしない。
                        continue;
                    }
                }
            }
            output.items[written] = point;
            if (point.ccc == 0) starter = written;
            last_ccc = point.ccc;
            written += 1;
        }
        output.shrinkRetainingCapacity(written);
    }
    return output;
}

fn lessCcc(_: void, a: Point, b: Point) bool {
    return a.ccc < b.ccc;
}

fn expectTransform(input: []const u8, expected: []const u8) !void {
    var result = try transform(std.testing.allocator, input);
    defer result.deinit();
    try std.testing.expectEqualStrings(expected, result.text);
}

test "検索用の変換は全角・カナ・合成・full foldingを揃え不可視文字を残す" {
    try expectTransform("ＡＢＣ１２ ｶﾞﾗｽ か\u{3099} ㌔ ① Straße ΟΣ ος İ", "abc12 ガラス が キロ 1 strasse οσ οσ i\u{307}");
    try expectTransform("か\u{309b} 〜 ～ ヒーター 読み取り", "か \u{3099} 〜 ~ ヒーター 読み取り");
    try expectTransform("👩\u{200d}💻 葛\u{e0100} \u{fe0f} \u{ad}\n\x00", "👩\u{200d}💻 葛\u{e0100} \u{fe0f} \u{ad}\n\x00");
    try expectTransform("\u{1100}\u{1161}\u{11a8}", "각");
    try expectTransform("a\u{315}\u{300}", "à\u{315}");
    try std.testing.expectError(error.InvalidUtf8, transform(std.testing.allocator, "\xff"));
}

test "展開・合成・並べ替えの一致区間を原文のバイト区間へ戻す" {
    const cases = .{
        .{ "前㌔後", "ロ", @as(usize, 3), @as(usize, 6) },
        .{ "前ｶﾞ後", "ガ", @as(usize, 3), @as(usize, 9) },
        .{ "前か\u{3099}後", "が", @as(usize, 3), @as(usize, 9) },
        .{ "a\u{315}\u{300}", "\u{315}", @as(usize, 1), @as(usize, 3) },
        .{ "a\u{315}\u{300}", "à", @as(usize, 0), @as(usize, 5) },
        .{ "ß", "s", @as(usize, 0), @as(usize, 2) },
        .{ "前\nＡＢＣ後", "abc", @as(usize, 4), @as(usize, 13) },
    };
    inline for (cases) |case| {
        var result = try transform(std.testing.allocator, case[0]);
        defer result.deinit();
        const start = std.mem.indexOf(u8, result.text, case[1]).?;
        try std.testing.expectEqual(Span{ .start = case[2], .end = case[3] }, result.originalSpan(start, start + case[1].len));
    }
}

fn allocationScenario(allocator: Allocator) !void {
    var result = try transform(allocator, "Ａｶﾞßa\u{315}\u{300}");
    defer result.deinit();
}

test "正規化のallocation failureで途中の所有メモリを残さない" {
    try std.testing.checkAllAllocationFailures(std.testing.allocator, allocationScenario, .{});
}

fn fallbackAllocationScenario(allocator: Allocator) !void {
    var result = try transform(allocator, "ﷺ");
    defer result.deinit();
    try std.testing.expectEqualStrings("صلى الله عليه وسلم", result.text);
    try std.testing.expectEqual(@as(usize, 18), result.points.len);
    for (result.points) |point| {
        try std.testing.expectEqual(@as(u32, 0), point.start);
        try std.testing.expectEqual(@as(u32, 3), point.end);
    }
    try std.testing.expectEqual(Span{ .start = 0, .end = 3 }, result.originalSpan(0, result.text.len));
}

test "8コードポイントを超える分解は全量再試行し確保失敗でも解放する" {
    // U+FDFAは18コードポイントへ展開し、スタック領域からの切替を通る。
    try fallbackAllocationScenario(std.testing.allocator);
    try std.testing.checkAllAllocationFailures(std.testing.allocator, fallbackAllocationScenario, .{});
}

fn expectExpansionLimit(cp: u21, fold: bool, expected: [2]u21) !void {
    const allocator = std.testing.allocator;
    const repeated = max_codepoints / 2;
    // 入力は上限内に保ち、最後の1文字だけで中間出力を上限+1にする。
    const input = try allocator.alloc(Point, repeated + 1);
    defer allocator.free(input);
    const width = try std.unicode.utf8CodepointSequenceLength(cp);
    for (input[0..repeated], 0..) |*point, i| {
        point.* = .{ .cp = cp, .ccc = 0, .start = @intCast(i * width), .end = @intCast((i + 1) * width) };
    }
    input[repeated] = .{ .cp = 'a', .ccc = 0, .start = @intCast(repeated * width), .end = @intCast(repeated * width + 1) };
    try std.testing.expect(input.len <= max_codepoints);
    {
        var exact = try mapped(allocator, input[0..repeated], fold, !fold);
        defer exact.deinit(allocator);
        try std.testing.expectEqual(max_codepoints, exact.items.len);
        try std.testing.expectEqual(expected[0], exact.items[0].cp);
        try std.testing.expectEqual(expected[1], exact.items[1].cp);
        try std.testing.expectEqual(expected[0], exact.items[exact.items.len - 2].cp);
        try std.testing.expectEqual(expected[1], exact.items[exact.items.len - 1].cp);
    }
    try std.testing.expectError(error.NormalizationTooLarge, mapped(allocator, input, fold, !fold));
}

test "分解の中間表現は実上限ちょうどで成功し1コードポイント超過を拒否する" {
    // ㌔はNFKCでキ・ロに展開し、合成でも2コードポイントのまま。
    try expectExpansionLimit('㌔', false, .{ 'キ', 'ロ' });
}

test "foldingの中間表現は実上限ちょうどで成功し1コードポイント超過を拒否する" {
    // ßは前段のNFKCでは増えず、foldingで初めてs・sに展開する。
    const nfkc = try testStage("ß", false);
    defer std.testing.allocator.free(nfkc);
    try std.testing.expectEqualStrings("ß", nfkc);
    try expectExpansionLimit('ß', true, .{ 's', 's' });
}

fn isAscii(text: []const u8) bool {
    for (text) |byte| {
        if (byte >= 128) return false;
    }
    return true;
}

fn parseCodepoints(allocator: Allocator, field: []const u8) ![]u8 {
    var bytes: std.ArrayList(u8) = .empty;
    errdefer bytes.deinit(allocator);
    var tokens = std.mem.tokenizeAny(u8, field, " \t\r");
    while (tokens.next()) |token| {
        const cp = try std.fmt.parseInt(u21, token, 16);
        var encoded: [4]u8 = undefined;
        const len = try std.unicode.utf8Encode(cp, &encoded);
        try bytes.appendSlice(allocator, encoded[0..len]);
    }
    return bytes.toOwnedSlice(allocator);
}

fn referenceMap(text: []const u8, fold: bool) ![]u8 {
    var out: ?[*]u8 = null;
    const length = zigurui_reference_map(text.ptr, @intCast(text.len), &out, fold);
    if (length < 0) return error.ReferenceNormalizationFailed;
    defer utf8proc_free(out);
    return std.testing.allocator.dupe(u8, out.?[0..@intCast(length)]);
}

fn referenceTransform(text: []const u8) ![]u8 {
    const first = try referenceMap(text, false);
    defer std.testing.allocator.free(first);
    const folded = try referenceMap(first, true);
    defer std.testing.allocator.free(folded);
    return referenceMap(folded, false);
}

fn testStage(text: []const u8, fold: bool) ![]u8 {
    const allocator = std.testing.allocator;
    var points: std.ArrayList(Point) = .empty;
    defer points.deinit(allocator);
    var it = std.unicode.Utf8View.initUnchecked(text).iterator();
    while (it.nextCodepoint()) |cp| try points.append(allocator, .{ .cp = cp, .ccc = 0, .start = 0, .end = 0 });
    var result = try mapped(allocator, points.items, fold, !fold);
    defer result.deinit(allocator);
    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(allocator);
    for (result.items) |point| {
        var encoded: [4]u8 = undefined;
        const len = try std.unicode.utf8Encode(point.cp, &encoded);
        try out.appendSlice(allocator, encoded[0..len]);
    }
    return out.toOwnedSlice(allocator);
}

test "Unicode18 NormalizationTestの全5列でNFKC期待値と全体変換を照合する" {
    const allocator = std.testing.allocator;
    var lines = std.mem.splitScalar(u8, @embedFile("NormalizationTest"), '\n');
    var tested: usize = 0;
    while (lines.next()) |line| {
        if (line.len == 0 or line[0] == '#' or line[0] == '@') continue;
        var fields = std.mem.splitScalar(u8, line, ';');
        var columns: [5][]u8 = undefined;
        var initialized: usize = 0;
        defer for (columns[0..initialized]) |column| allocator.free(column);
        for (&columns) |*column| {
            column.* = try parseCodepoints(allocator, fields.next().?);
            initialized += 1;
        }
        for (columns) |column| {
            const normalized = try testStage(column, false);
            defer allocator.free(normalized);
            try std.testing.expectEqualStrings(columns[3], normalized);
            const expected = try referenceTransform(column);
            defer allocator.free(expected);
            var actual = try transform(allocator, column);
            defer actual.deinit();
            try std.testing.expectEqualStrings(expected, actual.text);
        }
        tested += 1;
    }
    try std.testing.expect(tested > 19000);
}

test "Unicode18 CaseFoldingのCとFを照合しlocale固有Tとsimple Sを使わない" {
    const allocator = std.testing.allocator;
    var lines = std.mem.splitScalar(u8, @embedFile("CaseFoldingTest"), '\n');
    var tested: usize = 0;
    while (lines.next()) |line| {
        if (line.len == 0 or line[0] == '#') continue;
        var fields = std.mem.splitScalar(u8, line, ';');
        const source = fields.next().?;
        const status = std.mem.trim(u8, fields.next().?, " ");
        if (!std.mem.eql(u8, status, "C") and !std.mem.eql(u8, status, "F")) continue;
        const input = try parseCodepoints(allocator, source);
        defer allocator.free(input);
        const expected = try parseCodepoints(allocator, fields.next().?);
        defer allocator.free(expected);
        const actual = try testStage(input, true);
        defer allocator.free(actual);
        try std.testing.expectEqualStrings(expected, actual);
        tested += 1;
    }
    try std.testing.expect(tested > 1500);
}

test "長い結合列の安定順とblockingが全体変換と一致する" {
    const allocator = std.testing.allocator;
    var input: std.ArrayList(u8) = .empty;
    defer input.deinit(allocator);
    try input.appendSlice(allocator, "A");
    for (0..512) |_| try input.appendSlice(allocator, "\u{315}\u{300}\u{323}");
    const expected = try referenceTransform(input.items);
    defer allocator.free(expected);
    var result = try transform(allocator, input.items);
    defer result.deinit();
    try std.testing.expectEqualStrings(expected, result.text);
}
