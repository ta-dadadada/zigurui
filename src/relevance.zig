//! 一致済み文書の説明可能な順位を計算する。検索集合・原文位置・保存形式を変更しない。
const std = @import("std");
const search = @import("search.zig");
const normalize = @import("normalize.zig");
const frontmatter = @import("frontmatter.zig");
const profile = @import("profile.zig");
const A = std.mem.Allocator;
pub const max_fields = 32;
pub const max_terms = 64;
pub const max_points = 1_000_000;
pub const max_occurrences = 100_000;
pub const algorithm = "relevance-v1";
/// 保存原文から抽出するfieldの種類。metadataだけ任意のキー経路を持つ。
pub const Kind = enum { body, heading, file_name, metadata };
/// pointerはmetadataの配列透過キー経路。それ以外のkindではnull。設定所有者から借用する。
pub const Field = struct { kind: Kind, pointer: ?[]const u8 = null, weight: f64 = 1 };
const defaults = [_]Field{ .{ .kind = .body }, .{ .kind = .heading, .weight = 2 }, .{ .kind = .file_name } };
/// fieldsとpointerは呼び出し元から借用する。重みの契約はvalidateが所有する。
pub const Settings = struct {
    fields: []const Field = &defaults,
    proximity_weight: f64 = 1,
    ordered_weight: f64 = 1,

    /// field数・重複・経路・有限非負の重み上限を検証する。確保は行わない。
    pub fn validate(self: Settings) !void {
        if (self.fields.len == 0 or self.fields.len > max_fields) return error.InvalidRelevanceConfiguration;
        if (!validWeight(self.proximity_weight) or !validWeight(self.ordered_weight)) return error.InvalidRelevanceConfiguration;
        for (self.fields, 0..) |field, i| {
            if (!validWeight(field.weight)) return error.InvalidRelevanceConfiguration;
            if (field.kind == .metadata) {
                const pointer = field.pointer orelse return error.InvalidRelevanceConfiguration;
                if (std.mem.indexOfScalar(u8, pointer, 0) != null or (frontmatter.selectorKind(pointer) catch return error.InvalidRelevanceConfiguration) != .metadata) return error.InvalidRelevanceConfiguration;
            } else if (field.pointer != null) return error.InvalidRelevanceConfiguration;
            for (self.fields[0..i]) |previous| {
                if (previous.kind == field.kind and (field.kind != .metadata or std.mem.eql(u8, previous.pointer.?, field.pointer.?))) return error.InvalidRelevanceConfiguration;
            }
        }
    }
};
fn validWeight(w: f64) bool {
    return std.math.isFinite(w) and w >= 0 and w <= 100;
}
/// start/endは検索表現のUnicodeコードポイント単位の半開区間。valueは0始まりの値番号。
pub const Window = struct { value: usize, start: usize, end: usize };
/// field内の最良値と各信号の寄与。matched_term_indicesはResultとともに解放する。
pub const FieldScore = struct {
    kind: Kind,
    pointer: ?[]const u8,
    weight: f64,
    matched_terms: usize = 0,
    matched_term_indices: []usize = &.{},
    coverage_value: ?usize = null,
    coverage: f64 = 0,
    window: ?Window = null,
    ordered_window: ?Window = null,
    proximity: f64 = 0,
    ordered: f64 = 0,
    coverage_score: f64 = 0,
    proximity_score: f64 = 0,
    ordered_score: f64 = 0,
    total: f64 = 0,
};
/// fieldsと各matched_term_indicesを所有し、path/pointerは文書と設定から借用する。配列全体はdeinitで解放する。
pub const Result = struct { path: []const u8, offset: usize, end: usize, doc_id: usize, term_count: usize, total: f64, fields: []FieldScore };
fn freeFields(a: A, fields: []FieldScore) void {
    for (fields) |f| a.free(f.matched_term_indices);
    a.free(fields);
}
/// rankが返した配列と内訳を、生成時と同じallocatorで解放する。
pub fn deinit(a: A, results: []Result) void {
    for (results) |r| freeFields(a, r.fields);
    a.free(results);
}

const Query = struct {
    terms: []const []const u8,
    lengths: [max_terms]usize = undefined,
    total_length: usize = 0,

    fn init(terms: []const []const u8) !Query {
        const phase = profile.enter(.query_prepare);
        defer profile.leave(phase);
        if (terms.len == 0 or terms.len > max_terms) return error.RelevanceLimitExceeded;
        var result: Query = .{ .terms = terms };
        for (terms, 0..) |term, i| {
            result.lengths[i] = try std.unicode.utf8CountCodepoints(term);
            result.total_length += result.lengths[i];
        }
        return result;
    }
};

/// 全件採点に成功してからmatchesだけを並べ替える。失敗時にmatchesは変化しない。
/// 戻り値は呼び出し元がdeinitする。termsは検索と共有する準備済みの異なる語句。
pub fn rank(a: A, documents: []const search.Document, matches: []search.Match, terms: []const []const u8, mode: search.Mode, settings: Settings, diagnostic: ?*?frontmatter.Diagnostic) ![]Result {
    try settings.validate();
    const query = try Query.init(terms);
    const results = try a.alloc(Result, matches.len);
    var done: usize = 0;
    errdefer {
        for (results[0..done]) |r| freeFields(a, r.fields);
        a.free(results);
    }
    for (matches, results) |match, *result| {
        const doc = documents[match.doc_id];
        const fields = try a.alloc(FieldScore, settings.fields.len);
        result.* = .{ .path = doc.path, .offset = match.offset, .end = match.end, .doc_id = match.doc_id, .term_count = terms.len, .total = 0, .fields = fields };
        done += 1;
        for (settings.fields, fields) |field, *score| score.* = .{ .kind = field.kind, .pointer = field.pointer, .weight = field.weight };
        // metadata設定の有無で解析を決め、重み0でもfail-closedにする。
        const needs_metadata = for (settings.fields) |field| {
            if (field.kind == .metadata) break true;
        } else false;
        var metadata: ?frontmatter.Metadata = blk: {
            const phase = profile.enter(.field_extract);
            defer profile.leave(phase);
            break :blk if (needs_metadata) frontmatter.parse(a, doc.text) catch |err| {
                if (diagnostic) |out| out.* = .{ .document_index = match.doc_id, .context = .frontmatter, .reason = err };
                return err;
            } else null;
        };
        defer if (metadata) |*m| m.deinit(a);
        for (settings.fields, fields) |field, *score| {
            const phase = profile.enter(.field_extract);
            defer profile.leave(phase);
            var values: std.ArrayList([]const u8) = .empty;
            defer values.deinit(a);
            switch (field.kind) {
                .file_name => try values.append(a, std.fs.path.basename(doc.path)),
                .metadata => try metadata.?.strings(a, field.pointer.?, &values),
                .body => {
                    const start = frontmatter.bodyStart(doc.text) catch doc.text.len;
                    try values.append(a, doc.text[start..]);
                },
                .heading => try @import("ranking.zig").headings(a, doc.text, &values),
            }
            for (values.items, 0..) |text, value| try scoreValue(a, score, text, value, &query, mode, settings, field.kind == .body);
            score.coverage_score = field.weight * score.coverage;
            score.proximity_score = settings.proximity_weight * field.weight * score.proximity;
            score.ordered_score = settings.ordered_weight * field.weight * score.ordered;
            score.total = score.coverage_score + score.proximity_score + score.ordered_score;
            result.total += score.total;
        }
    }
    const phase = profile.enter(.rank_sort);
    defer profile.leave(phase);
    std.mem.sort(Result, results, {}, struct {
        fn less(_: void, x: Result, y: Result) bool {
            if (x.total != y.total) return x.total > y.total;
            return std.mem.lessThan(u8, x.path, y.path);
        }
    }.less);
    for (results, matches) |r, *m| m.* = .{ .doc_id = r.doc_id, .offset = r.offset, .end = r.end };
    return results;
}

fn scoreValue(a: A, score: *FieldScore, raw: []const u8, value: usize, query: *const Query, mode: search.Mode, settings: Settings, line_boundaries: bool) !void {
    const terms = query.terms;
    const previous = profile.enter(.field_normalize);
    var normalized: ?normalize.Result = if (mode == .normalized) normalize.transform(a, raw) catch |err| {
        profile.leave(previous);
        return err;
    } else null;
    defer if (normalized) |*n| n.deinit();
    const text = if (normalized) |n| n.text else raw;
    profile.leave(previous);
    const phase = profile.enter(.scoring);
    defer profile.leave(phase);
    if (try std.unicode.utf8CountCodepoints(text) > max_points) return error.RelevanceLimitExceeded;
    var covered: usize = 0;
    for (terms) |term| {
        if (std.mem.indexOf(u8, text, term) != null) covered += 1;
    }
    if (covered > score.matched_terms) {
        const indices = try a.alloc(usize, covered);
        var i: usize = 0;
        for (terms, 0..) |term, index| {
            if (std.mem.indexOf(u8, text, term) != null) {
                indices[i] = index;
                i += 1;
            }
        }
        a.free(score.matched_term_indices);
        score.matched_term_indices = indices;
        score.matched_terms = covered;
        score.coverage_value = value;
        score.coverage = @as(f64, @floatFromInt(covered)) / @as(f64, @floatFromInt(terms.len));
    }
    if (terms.len < 2 or (settings.proximity_weight == 0 and settings.ordered_weight == 0)) return;
    const prox = profile.enter(.proximity);
    defer profile.leave(prox);
    var lines = std.mem.splitScalar(u8, text, '\n');
    var base: usize = 0;
    var count: usize = 0;
    if (line_boundaries) {
        while (lines.next()) |line| {
            try proximity(a, score, line, value, base, query, settings, &count);
            base += try std.unicode.utf8CountCodepoints(line) + 1;
        }
    } else try proximity(a, score, text, value, 0, query, settings, &count);
}
const Occ = struct { start: usize, end: usize, term: usize };
fn improve(window: *?Window, value: usize, start: usize, end: usize) void {
    if (window.*) |old| {
        const width = end - start;
        const previous = old.end - old.start;
        if (width > previous or (width == previous and (value > old.value or (value == old.value and start >= old.start)))) return;
    }
    window.* = .{ .value = value, .start = start, .end = end };
}
fn signal(window: ?Window, length: usize) f64 {
    const w = window orelse return 0;
    return 1 / @as(f64, @floatFromInt(1 + (w.end - w.start -| length)));
}
fn proximity(a: A, score: *FieldScore, text: []const u8, value: usize, base: usize, query: *const Query, settings: Settings, count: *usize) !void {
    const terms = query.terms;
    const lengths = query.lengths[0..terms.len];
    const sum = query.total_length;
    var occurrences: std.ArrayList(Occ) = .empty;
    defer occurrences.deinit(a);
    var lists: [max_terms]std.ArrayList(usize) = @splat(.empty);
    defer for (lists[0..terms.len]) |*list| list.deinit(a);
    var complete = true;
    for (terms, 0..) |term, t| {
        var offset: usize = 0;
        var previous: usize = 0;
        var point: usize = 0;
        while (std.mem.indexOfPos(u8, text, offset, term)) |position| {
            count.* += 1;
            if (count.* > max_occurrences) return error.RelevanceLimitExceeded;
            point += try std.unicode.utf8CountCodepoints(text[previous..position]);
            previous = position;
            try occurrences.append(a, .{ .start = point, .end = point + lengths[t], .term = t });
            try lists[t].append(a, point);
            offset = position + @as(usize, try std.unicode.utf8ByteSequenceLength(text[position]));
        }
        if (lists[t].items.len == 0) complete = false;
    }
    if (!complete) return;
    if (settings.proximity_weight != 0) {
        std.mem.sort(Occ, occurrences.items, {}, struct {
            fn less(_: void, x: Occ, y: Occ) bool {
                if (x.end != y.end) return x.end < y.end;
                if (x.start != y.start) return x.start < y.start;
                return x.term < y.term;
            }
        }.less);
        var latest: [max_terms]?usize = @splat(null);
        for (occurrences.items) |occ| {
            latest[occ.term] = if (latest[occ.term]) |old| @max(old, occ.start) else occ.start;
            var start = occ.start;
            const all = for (latest[0..terms.len]) |position| {
                start = @min(start, position orelse break false);
            } else true;
            if (all) improve(&score.window, value, base + start, base + occ.end);
        }
        score.proximity = signal(score.window, sum);
    }
    if (settings.ordered_weight != 0) {
        // 最初の語句の全出現を試し、後続の最早開始を選ぶ。終端は包含も考慮した最大値。
        for (lists[0].items) |start| {
            var position = start;
            var end = start + lengths[0];
            const all = for (lists[1..terms.len], lengths[1..terms.len]) |list, length| {
                var low: usize = 0;
                var high = list.items.len;
                while (low < high) {
                    const mid = low + (high - low) / 2;
                    if (list.items[mid] < position) low = mid + 1 else high = mid;
                }
                if (low == list.items.len) break false;
                position = list.items[low];
                end = @max(end, position + length);
            } else true;
            if (all) improve(&score.ordered_window, value, base + start, base + end);
        }
        score.ordered = signal(score.ordered_window, sum);
    }
}

fn exerciseRank(a: A) !void {
    const docs = [_]search.Document{
        .{ .path = "a.md", .text = "---\nlabel: [ガラス, 断熱, 10, false, null]\n---\nガラス\n断熱" },
        .{ .path = "b.md", .text = "---\nlabel: [ガラス断熱, ガラス断熱]\n---\nガラス___断熱 ガラス断熱\n# ｶﾞﾗｽ断熱" },
    };
    var matches = [_]search.Match{ .{ .doc_id = 0, .offset = 0, .end = 3 }, .{ .doc_id = 1, .offset = 4, .end = 7 } };
    const before = matches;
    const settings: Settings = .{ .fields = &.{ .{ .kind = .body }, .{ .kind = .metadata, .pointer = "/label", .weight = 2 }, .{ .kind = .heading } } };
    const results = rank(a, &docs, &matches, &.{ "ガラス", "断熱" }, .normalized, settings, null) catch |err| {
        try std.testing.expectEqualDeep(before, matches);
        return err;
    };
    defer deinit(a, results);
    try std.testing.expectEqual(@as(usize, 1), matches[0].doc_id);
    try std.testing.expectEqual(@as(usize, 4), matches[0].offset);
    try std.testing.expectEqual(@as(usize, 7), matches[0].end);
    try std.testing.expectEqual(@as(f64, 0.5), results[1].fields[1].coverage);
    try std.testing.expectEqual(@as(f64, 1), results[1].fields[0].coverage);
    try std.testing.expectEqual(@as(f64, 0), results[1].fields[0].proximity);
    try std.testing.expectEqual(@as(f64, 1), results[0].fields[1].coverage);
    for (results) |r| {
        var sum: f64 = 0;
        for (r.fields) |f| {
            try std.testing.expectEqual(f.total, f.coverage_score + f.proximity_score + f.ordered_score);
            sum += f.total;
        }
        try std.testing.expectEqual(r.total, sum);
    }
}
test "field境界・混在配列・正規化・位置維持・確保失敗" {
    try exerciseRank(std.testing.allocator);
    try std.testing.checkAllAllocationFailures(std.testing.allocator, exerciseRank, .{});
}
test "最小区間と順序は全出現と最大終端を使う" {
    const cases = [_]struct { text: []const u8, terms: []const []const u8, start: usize, end: usize, ordered: ?usize }{
        .{ .text = "a_____b_ab", .terms = &.{ "a", "b" }, .start = 8, .end = 10, .ordered = 8 },
        .{ .text = "ba", .terms = &.{ "a", "b" }, .start = 0, .end = 2, .ordered = null },
        .{ .text = "abcd", .terms = &.{ "abcd", "ab" }, .start = 0, .end = 4, .ordered = 0 },
        .{ .text = "ababa", .terms = &.{ "aba", "bab" }, .start = 0, .end = 4, .ordered = 0 },
        .{ .text = "abcdef___x", .terms = &.{ "abcdef", "abc", "x" }, .start = 0, .end = 10, .ordered = 0 },
        .{ .text = "ｶﾞﾗｽ断熱", .terms = &.{ "ガラス", "断熱" }, .start = 0, .end = 5, .ordered = 0 },
    };
    for (cases) |case| {
        var score: FieldScore = .{ .kind = .body, .pointer = null, .weight = 1 };
        defer std.testing.allocator.free(score.matched_term_indices);
        const query = try Query.init(case.terms);
        try scoreValue(std.testing.allocator, &score, case.text, 0, &query, .normalized, .{}, true);
        try std.testing.expectEqual(case.start, score.window.?.start);
        try std.testing.expectEqual(case.end, score.window.?.end);
        if (case.ordered) |start| {
            try std.testing.expectEqual(start, score.ordered_window.?.start);
            try std.testing.expectEqual(case.end, score.ordered_window.?.end);
        } else try std.testing.expectEqual(null, score.ordered_window);
    }
}
test "metadata診断・重み0・後半失敗で未変更" {
    const docs = [_]search.Document{ .{ .path = "a", .text = "---\nx: ok\n---\na" }, .{ .path = "b", .text = "---\nx: [\n---\na" } };
    var matches = [_]search.Match{ .{ .doc_id = 0, .offset = 0, .end = 1 }, .{ .doc_id = 1, .offset = 0, .end = 1 } };
    const before = matches;
    var diag: ?frontmatter.Diagnostic = null;
    try std.testing.expectError(error.InvalidFrontmatter, rank(std.testing.allocator, &docs, &matches, &.{"a"}, .literal, .{ .fields = &.{.{ .kind = .metadata, .pointer = "/x", .weight = 0 }} }, &diag));
    try std.testing.expectEqualDeep(before, matches);
    try std.testing.expectEqual(@as(usize, 1), diag.?.document_index);
}
test "重み・pointerと資源上限" {
    try std.testing.expectError(error.InvalidRelevanceConfiguration, (Settings{ .ordered_weight = std.math.nan(f64) }).validate());
    try std.testing.expectError(error.InvalidRelevanceConfiguration, (Settings{ .proximity_weight = -1 }).validate());
    try std.testing.expectError(error.InvalidRelevanceConfiguration, (Settings{ .fields = &.{.{ .kind = .metadata, .pointer = "/a~2" }} }).validate());
    const a = std.testing.allocator;
    const large = try a.alloc(u8, max_points + 1);
    defer a.free(large);
    @memset(large, 'a');
    var score: FieldScore = .{ .kind = .body, .pointer = null, .weight = 1 };
    defer a.free(score.matched_term_indices);
    const one = try Query.init(&.{"a"});
    const two = try Query.init(&.{ "a", "aa" });
    try std.testing.expectError(error.RelevanceLimitExceeded, scoreValue(a, &score, large, 0, &one, .literal, .{}, true));
    try std.testing.expectError(error.RelevanceLimitExceeded, scoreValue(a, &score, large[0..max_occurrences], 0, &two, .literal, .{}, true));
}
