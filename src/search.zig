//! Unicodeコードポイント2-gramによる候補抽出と、部分一致検索を行う。
//! 全文走査を独立した正解の基準として提供し、索引検索と同じ結果契約を持つ。

const std = @import("std");
const Allocator = std.mem.Allocator;

/// 検索対象の文書。パスとUTF-8本文の所有権は呼び出し側にある。
pub const Document = struct {
    path: []const u8,
    text: []const u8,
};

/// 2コードポイントのgramと、それを含む文書IDの対応。
/// gramは文書または保存データを借用し、doc_idsは昇順で重複しない。
pub const Posting = struct {
    gram: []const u8,
    doc_ids: []const usize,
};

/// 一致した文書と、その本文における最初の一致位置。
pub const Match = struct {
    doc_id: usize,
    /// UTF-8のバイト位置。N-gramの分割に使うコードポイントの個数ではない。
    offset: usize,
};

/// 文書配列・本文・gramを借用し、postings配列と各doc_ids配列を所有する。
/// 借用データは索引を使う間、変更・解放しない。所有配列はdeinitで解放する。
pub const Index = struct {
    allocator: Allocator,
    documents: []const Document,
    /// 二分探索の前提としてgramのバイト辞書順に並び、重複しない。
    postings: []const Posting,

    /// 文書から索引を構築する。gramは本文のスライスを借用する。
    /// 本文が不正なUTF-8ならInvalidUtf8、確保失敗ならOutOfMemoryを返す。
    /// 成功後は呼び出し側がdeinitする。失敗時に所有メモリは残さない。
    pub fn init(allocator: Allocator, documents: []const Document) !Index {
        var map = std.StringHashMap(std.ArrayList(usize)).init(allocator);
        defer {
            var it = map.valueIterator();
            while (it.next()) |ids| ids.deinit(allocator);
            map.deinit();
        }
        for (documents, 0..) |doc, doc_id| {
            if (!std.unicode.utf8ValidateSlice(doc.text)) return error.InvalidUtf8;
            var grams = Grams.init(doc.text);
            while (grams.next()) |gram| {
                const entry = try map.getOrPut(gram);
                if (!entry.found_existing) entry.value_ptr.* = .empty;
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
        return .{ .allocator = allocator, .documents = documents, .postings = postings };
    }

    /// 所有配列のみを解放してselfを無効化する。借用した文書や文字列は解放しない。
    pub fn deinit(self: *Index) void {
        for (self.postings) |posting| self.allocator.free(posting.doc_ids);
        self.allocator.free(self.postings);
        self.* = undefined;
    }

    /// 大文字・小文字を区別する連続部分一致を、文書ID順で1文書1件返す。
    /// 1コードポイントのqueryは全文走査する。結果配列は呼び出し側がallocator.freeする。
    /// 空queryはEmptyQuery、不正UTF-8はInvalidUtf8、確保失敗はOutOfMemory。
    pub fn search(self: *const Index, allocator: Allocator, query: []const u8) ![]Match {
        try validateQuery(query);
        var grams = Grams.init(query);
        var candidates: ?[]const usize = null;
        while (grams.next()) |gram| {
            const ids = self.lookup(gram) orelse return allocator.alloc(Match, 0);
            // 最小の文書ID一覧を起点にし、後段で残りのgramを照合する量を減らす。
            if (candidates == null or ids.len < candidates.?.len) candidates = ids;
        }
        // 1コードポイントの検索は全文走査に切り替える。
        const ids = candidates orelse return self.scan(allocator, query);
        var matches: std.ArrayList(Match) = .empty;
        errdefer matches.deinit(allocator);
        for (ids) |doc_id| {
            var required = Grams.init(query);
            var all_present = true;
            while (required.next()) |gram| {
                if (!containsId(self.lookup(gram).?, doc_id)) {
                    all_present = false;
                    break;
                }
            }
            if (!all_present) continue;
            // 各gramが別々の位置に現れる文書も候補になるため、原文で連続一致を確認する。
            if (std.mem.indexOf(u8, self.documents[doc_id].text, query)) |offset|
                try matches.append(allocator, .{ .doc_id = doc_id, .offset = offset });
        }
        return matches.toOwnedSlice(allocator);
    }

    /// postingsを使わず全本文を走査する。結果の順序・一致条件・エラーはsearchと同じ。
    /// 結果配列は呼び出し側がallocator.freeする。
    pub fn scan(self: *const Index, allocator: Allocator, query: []const u8) ![]Match {
        try validateQuery(query);
        var matches: std.ArrayList(Match) = .empty;
        errdefer matches.deinit(allocator);
        for (self.documents, 0..) |doc, doc_id| {
            if (std.mem.indexOf(u8, doc.text, query)) |offset|
                try matches.append(allocator, .{ .doc_id = doc_id, .offset = offset });
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
    try std.testing.expectEqual(Match{ .doc_id = 0, .offset = "日本語の".len }, matches[0]);
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
