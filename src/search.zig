//! Unicodeコードポイント2-gramによる候補抽出と、部分一致検索を行う。
//! 全文走査を独立した正解の基準として提供し、索引検索と同じ結果契約を持つ。

const std = @import("std");
const Allocator = std.mem.Allocator;
const normalize = @import("normalize.zig");

/// literalは原文、normalizedは固定版の検索用正規化表現で照合する。
pub const Mode = enum(u32) { literal = 0, normalized = 1 };

/// 検索対象の文書。パスとUTF-8本文の所有権は呼び出し側にある。
pub const Document = struct {
    path: []const u8,
    text: []const u8,
};

/// 2コードポイントのgramと、それを含む文書IDの対応。
/// gramはIndexの所有キーまたは保存データを借用し、doc_idsは昇順で重複しない。
pub const Posting = struct {
    gram: []const u8,
    doc_ids: []const usize,
};

/// 検索表現で最初に一致した箇所を覆う原文区間。normalizedでは原文順の最初とは限らない。
pub const Match = struct {
    doc_id: usize,
    /// UTF-8のバイト位置。N-gramの分割に使うコードポイントの個数ではない。
    offset: usize,
    /// 一致を覆う原文UTF-8バイト区間の終端（含まない）。
    end: usize,
};

/// 文書配列・本文を借用し、postings配列と各doc_ids配列を所有する。
/// 借用データは索引を使う間、変更・解放しない。所有配列はdeinitで解放する。
pub const Index = struct {
    allocator: Allocator,
    documents: []const Document,
    /// 二分探索の前提としてgramのバイト辞書順に並び、重複しない。
    postings: []const Posting,
    mode: Mode = .literal,
    /// 構築時はgramの複製を所有し、復元時は保存バッファを借用する。
    owns_grams: bool = false,

    /// literal索引を構築する。gramは複製して所有する。
    /// 本文が不正なUTF-8ならInvalidUtf8、確保失敗ならOutOfMemoryを返す。
    /// 成功後は呼び出し側がdeinitする。失敗時に所有メモリは残さない。
    pub fn init(allocator: Allocator, documents: []const Document) !Index {
        return initMode(allocator, documents, .literal);
    }

    /// 指定modeで構築する。原文は借用し、unique gramとpostingは所有する。
    pub fn initMode(allocator: Allocator, documents: []const Document, mode: Mode) !Index {
        var map = std.StringHashMap(std.ArrayList(usize)).init(allocator);
        var keys_transferred = false;
        defer {
            var it = map.iterator();
            while (it.next()) |entry| {
                entry.value_ptr.deinit(allocator);
                if (!keys_transferred) allocator.free(entry.key_ptr.*);
            }
            map.deinit();
        }
        for (documents, 0..) |doc, doc_id| {
            if (!std.unicode.utf8ValidateSlice(doc.text)) return error.InvalidUtf8;
            var normalized: ?normalize.Result = if (mode == .normalized) try normalize.transform(allocator, doc.text) else null;
            defer if (normalized) |*result| result.deinit();
            var grams = Grams.init(if (normalized) |result| result.text else doc.text);
            while (grams.next()) |gram| {
                const entry = try map.getOrPut(gram);
                if (!entry.found_existing) {
                    entry.value_ptr.* = .empty;
                    entry.key_ptr.* = allocator.dupe(u8, gram) catch |err| {
                        _ = map.remove(gram);
                        return err;
                    };
                }
                const ids = entry.value_ptr;
                if (ids.items.len == 0 or ids.items[ids.items.len - 1] != doc_id)
                    try ids.append(allocator, doc_id);
            }
        }
        const postings = try allocator.alloc(Posting, map.count());
        var initialized: usize = 0;
        errdefer {
            for (postings[0..initialized]) |posting| allocator.free(posting.doc_ids);
            allocator.free(postings);
        }
        var it = map.iterator();
        while (it.next()) |entry| {
            postings[initialized] = .{
                .gram = entry.key_ptr.*,
                .doc_ids = try entry.value_ptr.toOwnedSlice(allocator),
            };
            initialized += 1;
        }
        std.mem.sort(Posting, postings, {}, postingLessThan);
        keys_transferred = true;
        return .{ .allocator = allocator, .documents = documents, .postings = postings, .mode = mode, .owns_grams = true };
    }

    /// 所有配列と所有gramを解放してselfを無効化する。借用した文書・保存バッファは解放しない。
    pub fn deinit(self: *Index) void {
        for (self.postings) |posting| {
            self.allocator.free(posting.doc_ids);
            if (self.owns_grams) self.allocator.free(posting.gram);
        }
        self.allocator.free(self.postings);
        self.* = undefined;
    }

    /// 大文字・小文字を区別する連続部分一致を、文書ID順で1文書1件返す。
    /// 1コードポイントのqueryは全文走査する。結果配列は呼び出し側がallocator.freeする。
    /// 空queryはEmptyQuery、不正UTF-8はInvalidUtf8、確保失敗はOutOfMemory。
    pub fn search(self: *const Index, allocator: Allocator, query: []const u8) ![]Match {
        return self.searchMode(allocator, query, .literal);
    }

    /// 正規化索引のliteral検索は保存原文を走査する。逆方向は再構築が必要。
    /// normalizedは正規化本文で最初の一致を覆う原文区間を返す。
    pub fn searchMode(self: *const Index, allocator: Allocator, query: []const u8, mode: Mode) ![]Match {
        try validateQuery(query);
        if (mode == .normalized and self.mode != .normalized) return error.IndexModeMismatch;
        if (mode == .literal and self.mode == .normalized) return self.scanMode(allocator, query, mode);
        var normalized: ?normalize.Result = if (mode == .normalized) try normalize.transform(allocator, query) else null;
        defer if (normalized) |*result| result.deinit();
        const needle = if (normalized) |result| result.text else query;
        if (needle.len == 0) return error.EmptyQuery;
        var grams = Grams.init(needle);
        var candidates: ?[]const usize = null;
        while (grams.next()) |gram| {
            const ids = self.lookup(gram) orelse return allocator.alloc(Match, 0);
            if (candidates == null or ids.len < candidates.?.len) candidates = ids;
        }
        // 短語の分岐は入力ではなく正規化後のコードポイント数で決める。
        const ids = candidates orelse return self.scanPrepared(allocator, needle, mode);
        var matches: std.ArrayList(Match) = .empty;
        errdefer matches.deinit(allocator);
        for (ids) |doc_id| {
            var required = Grams.init(needle);
            var all_present = true;
            while (required.next()) |gram| {
                if (!containsId(self.lookup(gram).?, doc_id)) {
                    all_present = false;
                    break;
                }
            }
            if (!all_present) continue;
            if (try matchDocument(allocator, self.documents[doc_id].text, needle, mode)) |span|
                try matches.append(allocator, .{ .doc_id = doc_id, .offset = span.start, .end = span.end });
        }
        return matches.toOwnedSlice(allocator);
    }

    /// postingsを使わず全本文をliteralで走査する。結果は呼び出し側がfreeする。
    pub fn scan(self: *const Index, allocator: Allocator, query: []const u8) ![]Match {
        return self.scanMode(allocator, query, .literal);
    }

    /// 全文走査も同じ変換と位置復元を使う。self.modeやpostingsは参照しない。
    pub fn scanMode(self: *const Index, allocator: Allocator, query: []const u8, mode: Mode) ![]Match {
        try validateQuery(query);
        var normalized: ?normalize.Result = if (mode == .normalized) try normalize.transform(allocator, query) else null;
        defer if (normalized) |*result| result.deinit();
        const needle = if (normalized) |result| result.text else query;
        if (needle.len == 0) return error.EmptyQuery;
        return self.scanPrepared(allocator, needle, mode);
    }

    fn scanPrepared(self: *const Index, allocator: Allocator, needle: []const u8, mode: Mode) ![]Match {
        var matches: std.ArrayList(Match) = .empty;
        errdefer matches.deinit(allocator);
        for (self.documents, 0..) |doc, doc_id| {
            if (try matchDocument(allocator, doc.text, needle, mode)) |span|
                try matches.append(allocator, .{ .doc_id = doc_id, .offset = span.start, .end = span.end });
        }
        return matches.toOwnedSlice(allocator);
    }

    fn lookup(self: *const Index, gram: []const u8) ?[]const usize {
        var low: usize = 0;
        var high = self.postings.len;
        while (low < high) {
            const middle = low + (high - low) / 2;
            switch (std.mem.order(u8, self.postings[middle].gram, gram)) {
                .lt => low = middle + 1,
                .gt => high = middle,
                .eq => return self.postings[middle].doc_ids,
            }
        }
        return null;
    }
};

fn matchDocument(allocator: Allocator, text: []const u8, needle: []const u8, mode: Mode) !?normalize.Span {
    if (mode == .literal) {
        const offset = std.mem.indexOf(u8, text, needle) orelse return null;
        return .{ .start = offset, .end = offset + needle.len };
    }
    var result = try normalize.transform(allocator, text);
    defer result.deinit();
    const offset = std.mem.indexOf(u8, result.text, needle) orelse return null;
    return result.originalSpan(offset, offset + needle.len);
}

fn postingLessThan(_: void, a: Posting, b: Posting) bool {
    return std.mem.lessThan(u8, a.gram, b.gram);
}

fn validateQuery(query: []const u8) !void {
    if (query.len == 0) return error.EmptyQuery;
    if (!std.unicode.utf8ValidateSlice(query)) return error.InvalidUtf8;
}

fn containsId(ids: []const usize, doc_id: usize) bool {
    var low: usize = 0;
    var high = ids.len;
    while (low < high) {
        const middle = low + (high - low) / 2;
        if (ids[middle] < doc_id) low = middle + 1 else high = middle;
    }
    return low < ids.len and ids[low] == doc_id;
}

// UTF-8検証済みの文字列から、隣接する2コードポイントを借用する。
const Grams = struct {
    text: []const u8,
    offset: usize = 0,

    fn init(text: []const u8) Grams {
        return .{ .text = text };
    }

    fn next(self: *Grams) ?[]const u8 {
        if (self.offset >= self.text.len) return null;
        const start = self.offset;
        const first_len = std.unicode.utf8ByteSequenceLength(self.text[start]) catch unreachable;
        self.offset += first_len;
        if (self.offset >= self.text.len) return null;
        const second_len = std.unicode.utf8ByteSequenceLength(self.text[self.offset]) catch unreachable;
        return self.text[start .. self.offset + second_len];
    }
};

const fixture = [_]Document{
    .{ .path = "日本語.md", .text = "日本語の検索エンジン。Zigで検索。🐱猫" },
    .{ .path = "english.md", .text = "Zig search engine\naaaaa" },
    .{ .path = "false-positive.md", .text = "検索、索エ、エン、ンジ、ジン ab bc ca" },
    .{ .path = "empty.md", .text = "" },
    .{ .path = "unicode.md", .text = "e\u{301} と é、𠮷野家、🐱猫🐱" },
};

test "索引検索は日本語・英語・短語・絵文字・結合文字で全文走査と一致する" {
    const allocator = std.testing.allocator;
    var index = try Index.init(allocator, &fixture);
    defer index.deinit();
    const queries = [_][]const u8{ "検索エンジン", "検索", "索", "Zig", "zig", "engine", "aaaa", "a", "🐱猫", "猫🐱", "𠮷野", "e\u{301}", "é", "存在しない", "abc", "abca", "\n", " " };
    for (queries) |query| {
        const actual = try index.search(allocator, query);
        defer allocator.free(actual);
        const expected = try index.scan(allocator, query);
        defer allocator.free(expected);
        try std.testing.expectEqualSlices(Match, expected, actual);
    }
    const matches = try index.search(allocator, "検索エンジン");
    defer allocator.free(matches);
    try std.testing.expectEqual(@as(usize, 1), matches.len);
    try std.testing.expectEqual(Match{ .doc_id = 0, .offset = "日本語の".len, .end = "日本語の検索エンジン".len }, matches[0]);
}

test "繰り返しgramの文書IDは重複しない" {
    var index = try Index.init(std.testing.allocator, &fixture);
    defer index.deinit();
    try std.testing.expectEqualSlices(usize, &.{1}, index.lookup("aa").?);
}

test "空クエリ・不正UTF8を拒否する" {
    const allocator = std.testing.allocator;
    var index = try Index.init(allocator, &fixture);
    defer index.deinit();
    try std.testing.expectError(error.EmptyQuery, index.search(allocator, ""));
    try std.testing.expectError(error.EmptyQuery, index.scan(allocator, ""));
    try std.testing.expectError(error.InvalidUtf8, index.search(allocator, "\xff"));
    try std.testing.expectError(error.InvalidUtf8, index.scan(allocator, "\xc0\x80"));
    try std.testing.expectError(error.InvalidUtf8, Index.init(allocator, &.{.{ .path = "bad.md", .text = "\xff" }}));
}

test "空の文書群を検索できる" {
    const allocator = std.testing.allocator;
    var index = try Index.init(allocator, &.{});
    defer index.deinit();
    const matches = try index.search(allocator, "検索");
    defer allocator.free(matches);
    try std.testing.expectEqual(@as(usize, 0), matches.len);
}

fn allocationScenario(allocator: Allocator) !void {
    var index = try Index.init(allocator, &fixture);
    defer index.deinit();
    const matches = try index.search(allocator, "検索");
    defer allocator.free(matches);
    const scanned = try index.scan(allocator, "猫");
    defer allocator.free(scanned);
}

test "構築と検索はallocation failureでもリークしない" {
    try std.testing.checkAllAllocationFailures(std.testing.allocator, allocationScenario, .{});
}

test "多言語corpusの全コードポイント部分文字列で全文走査と一致する" {
    const allocator = std.testing.allocator;
    var index = try Index.init(allocator, &fixture);
    defer index.deinit();
    for (fixture) |doc| {
        var starts = std.unicode.Utf8View.initUnchecked(doc.text).iterator();
        while (starts.i < doc.text.len) {
            const start = starts.i;
            _ = starts.nextCodepointSlice();
            var ends = starts;
            while (true) {
                const query = doc.text[start..ends.i];
                const expected = try index.scan(allocator, query);
                defer allocator.free(expected);
                const actual = try index.search(allocator, query);
                defer allocator.free(actual);
                try std.testing.expectEqualSlices(Match, expected, actual);
                if (ends.nextCodepointSlice() == null) break;
            }
        }
    }
}

const normalized_fixture = [_]Document{
    .{ .path = "a.md", .text = "前\nＡＢＣ後" },
    .{ .path = "b.md", .text = "前ｶﾞ後" },
    .{ .path = "c.md", .text = "前㌔後" },
    .{ .path = "d.md", .text = "a\u{315}\u{300}" },
    .{ .path = "e.md", .text = "ß" },
    .{ .path = "f.md", .text = "前か\u{3099}後" },
    .{ .path = "g.md", .text = "ab--bc" },
    .{ .path = "h.md", .text = "" },
    .{ .path = "i.md", .text = "👩\u{200d}💻 葛\u{e0100}" },
};

test "正規化索引とscanが独立した原文区間期待値と一致する" {
    const allocator = std.testing.allocator;
    var index = try Index.initMode(allocator, &normalized_fixture, .normalized);
    defer index.deinit();
    const Case = struct { query: []const u8, expected: []const Match };
    const cases = [_]Case{
        .{ .query = "abc", .expected = &.{.{ .doc_id = 0, .offset = 4, .end = 13 }} },
        .{ .query = "ＡＢＣ", .expected = &.{.{ .doc_id = 0, .offset = 4, .end = 13 }} },
        .{ .query = "ガ", .expected = &.{.{ .doc_id = 1, .offset = 3, .end = 9 }} },
        .{ .query = "ｶﾞ", .expected = &.{.{ .doc_id = 1, .offset = 3, .end = 9 }} },
        .{ .query = "㌔", .expected = &.{.{ .doc_id = 2, .offset = 3, .end = 6 }} },
        .{ .query = "ロ", .expected = &.{.{ .doc_id = 2, .offset = 3, .end = 6 }} },
        .{ .query = "\u{315}", .expected = &.{.{ .doc_id = 3, .offset = 1, .end = 3 }} },
        .{ .query = "à", .expected = &.{.{ .doc_id = 3, .offset = 0, .end = 5 }} },
        .{ .query = "s", .expected = &.{.{ .doc_id = 4, .offset = 0, .end = 2 }} },
        .{ .query = "SS", .expected = &.{.{ .doc_id = 4, .offset = 0, .end = 2 }} },
        .{ .query = "が", .expected = &.{.{ .doc_id = 5, .offset = 3, .end = 9 }} },
        .{ .query = "abcd", .expected = &.{} },
        .{ .query = "か\u{309b}", .expected = &.{} },
        .{ .query = "👩💻", .expected = &.{} },
    };
    for (cases) |case| {
        const actual = try index.searchMode(allocator, case.query, .normalized);
        defer allocator.free(actual);
        const scanned = try index.scanMode(allocator, case.query, .normalized);
        defer allocator.free(scanned);
        try std.testing.expectEqualSlices(Match, case.expected, actual);
        try std.testing.expectEqualSlices(Match, case.expected, scanned);
    }
    const literal = try index.search(allocator, "abc");
    defer allocator.free(literal);
    try std.testing.expectEqual(@as(usize, 0), literal.len);
    try std.testing.expectError(error.EmptyQuery, index.searchMode(allocator, "", .normalized));
    try std.testing.expectError(error.InvalidUtf8, index.scanMode(allocator, "\xc0\x80", .normalized));
    var old_mode = try Index.init(allocator, &normalized_fixture);
    defer old_mode.deinit();
    try std.testing.expectError(error.IndexModeMismatch, old_mode.searchMode(allocator, "abc", .normalized));
}

fn normalizedAllocationScenario(allocator: Allocator) !void {
    const docs = [_]Document{.{ .path = "a.md", .text = "ＡＢＣｶﾞß" }};
    var index = try Index.initMode(allocator, &docs, .normalized);
    defer index.deinit();
    const matches = try index.searchMode(allocator, "ＡＢ", .normalized);
    defer allocator.free(matches);
    const scanned = try index.scanMode(allocator, "ガ", .normalized);
    defer allocator.free(scanned);
}

test "正規化gramと候補変換は確保失敗でもリークしない" {
    try std.testing.checkAllAllocationFailures(std.testing.allocator, normalizedAllocationScenario, .{});
}
