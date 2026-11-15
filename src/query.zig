//! 本文投影・候補検索・原文位置の復元・順位付けを組み立て、結果の所有権をまとめる。
const std = @import("std");
const search = @import("search.zig");
const frontmatter = @import("frontmatter.zig");
const ranking = @import("ranking.zig");
const relevance = @import("relevance.zig");
const profile = @import("profile.zig");
const Allocator = std.mem.Allocator;

/// 入力語句を借用する。singleは空白も含む一つの連続文字列、allは文書単位のAND。
pub const Terms = union(enum) { single: []const u8, all: []const []const u8 };
/// indexは転置一覧を使い、scanは全文走査する。modeによる既存の走査への切替も維持する。
pub const Source = enum { index, scan };
/// CLIの保存先・表示・指定有無から独立した検索条件。語句と設定は呼び出し中借用する。
pub const Request = struct {
    terms: Terms,
    mode: search.Mode = .literal,
    frontmatter: bool = false,
    rank: ranking.Order = .path,
    relevance_settings: relevance.Settings = .{},
};

/// matchesとscoresの配列を所有し、deinitで一括解放する。
/// scores内のpathとpointerは文書と設定を借用するため、Resultより先に解放しない。
/// relevanceでは0件でもscoresが存在し、それ以外の順位ではnullになる。
pub const Result = struct {
    allocator: Allocator,
    matches: []search.Match,
    scores: ?[]relevance.Result = null,

    /// 生成時のallocatorで所有配列を解放してselfを無効化する。借用元は解放しない。
    pub fn deinit(self: *Result) void {
        const phase = profile.enter(.cleanup);
        defer profile.leave(phase);
        if (self.scores) |scores| relevance.deinit(self.allocator, scores);
        self.allocator.free(self.matches);
        self.* = undefined;
    }
};

/// index・filters・requestを借用し、成功時だけ所有Resultを返す。失敗時は全作業メモリを解放する。
/// matchesのoffset/endは原文UTF-8バイト。入力索引・文書を変更しない。
/// diagnosticは開始時にclearし、YAML解析失敗時だけ対象文書と理由を設定する。
pub fn execute(allocator: Allocator, index: *const search.Index, request: Request, filters: []const frontmatter.Filter, source: Source, diagnostic: ?*?frontmatter.Diagnostic) !Result {
    if (diagnostic) |out| out.* = null;
    var projection: ?frontmatter.Projection = if (request.frontmatter or filters.len != 0) try frontmatter.Projection.init(allocator, index.documents, filters, request.frontmatter, diagnostic) else null;
    defer if (projection) |*value| value.deinit(allocator);
    // 投影は原文とpostingsを借用するだけで、Index.deinitは呼ばない。
    const projected: search.Index = if (projection) |value| .{ .allocator = allocator, .documents = value.documents, .postings = index.postings, .mode = index.mode, .eligible = value.eligible } else index.*;
    const terms: []const []const u8 = switch (request.terms) {
        .single => |term| &.{term},
        .all => |all| all,
    };
    var prepared: ?search.PreparedTerms = if (request.rank == .relevance) try search.PreparedTerms.init(allocator, terms, request.mode) else null;
    defer if (prepared) |*ready| ready.deinit(allocator);
    const matches = if (prepared) |*ready| try projected.findReady(allocator, ready, request.mode, source == .scan) else switch (request.terms) {
        .single => |term| if (source == .scan) try projected.scanMode(allocator, term, request.mode) else try projected.searchMode(allocator, term, request.mode),
        .all => |all| if (source == .scan) try projected.scanAll(allocator, all, request.mode) else try projected.searchAll(allocator, all, request.mode),
    };
    var result: Result = .{ .allocator = allocator, .matches = matches };
    errdefer result.deinit();
    if (projection) |value| value.restore(matches);
    if (request.rank == .heading) try ranking.sort(allocator, index.documents, matches, terms, request.mode);
    if (prepared) |ready| result.scores = try relevance.rank(allocator, index.documents, matches, ready.needles.items, request.mode, request.relevance_settings, diagnostic);
    return result;
}

test "本文投影はmetadataだけの一致を除きAND原文両端を復元する" {
    const allocator = std.testing.allocator;
    const prefix = "\xef\xbb\xbf---\r\ntitle: header-only\r\nstate: ready\r\n---\r\n";
    const documents = [_]search.Document{
        .{ .path = "a", .text = prefix ++ "前㌔後 ｶﾞ" },
        .{ .path = "b", .text = "---\nstate: draft\n---\nキロ ガ" },
        .{ .path = "c", .text = "キロ ガ" },
    };
    var index = try search.Index.initMode(allocator, &documents, .normalized);
    defer index.deinit();
    var filter = try frontmatter.Filter.init(allocator, "/state", "\"ready\"");
    defer filter.deinit(allocator);
    const request: Request = .{ .mode = .normalized, .terms = .{ .all = &.{ "ガ", "ロ" } }, .frontmatter = true };
    for ([_]bool{ false, true }) |scan| {
        var matches = try execute(allocator, &index, request, &.{filter}, if (scan) .scan else .index, null);
        defer matches.deinit();
        try std.testing.expectEqualSlices(search.Match, &.{.{ .doc_id = 0, .offset = prefix.len + 3, .end = prefix.len + 6 }}, matches.matches);
    }
    var single: Request = .{ .mode = .normalized, .terms = .{ .single = "header-only" }, .frontmatter = true };
    var body = try execute(allocator, &index, single, &.{}, .index, null);
    defer body.deinit();
    try std.testing.expectEqual(@as(usize, 0), body.matches.len);
    single.frontmatter = false;
    var raw = try execute(allocator, &index, single, &.{}, .index, null);
    defer raw.deinit();
    try std.testing.expectEqual(@as(usize, 1), raw.matches.len);
    single.frontmatter = true;
    single.mode = .literal;
    single.terms = .{ .single = "ｶﾞ" };
    var literal = try execute(allocator, &index, single, &.{}, .index, null);
    defer literal.deinit();
    try std.testing.expectEqualSlices(search.Match, &.{.{ .doc_id = 0, .offset = prefix.len + 10, .end = prefix.len + 16 }}, literal.matches);
}

test "候補外やfilter不一致の不正frontmatterも全体エラーにする" {
    const allocator = std.testing.allocator;
    const documents = [_]search.Document{
        .{ .path = "a", .text = "---\nstate: ready\n---\nknown" },
        .{ .path = "b", .text = "---\nstate: [broken\n---\nother" },
    };
    var index = try search.Index.init(allocator, &documents);
    defer index.deinit();
    const request: Request = .{ .terms = .{ .single = "nonexistent" }, .frontmatter = true };
    var filter = try frontmatter.Filter.init(allocator, "/state", "\"ready\"");
    defer filter.deinit(allocator);
    try std.testing.expectError(error.InvalidFrontmatter, execute(allocator, &index, request, &.{filter}, .index, null));
    try std.testing.expectError(error.InvalidFrontmatter, execute(allocator, &index, request, &.{filter}, .scan, null));
}

test "本文先頭の結合文字でも原文postingsが本文正規化の候補を落とさない" {
    const allocator = std.testing.allocator;
    const prefix = "---\nx: v\n---\n";
    const documents = [_]search.Document{.{ .path = "a", .text = prefix ++ "\u{315}\u{300}A" }};
    var index = try search.Index.initMode(allocator, &documents, .normalized);
    defer index.deinit();
    const request: Request = .{ .mode = .normalized, .frontmatter = true, .terms = .{ .all = &.{ "a", "\u{300}\u{315}" } } };
    for ([_]bool{ false, true }) |scan| {
        var matches = try execute(allocator, &index, request, &.{}, if (scan) .scan else .index, null);
        defer matches.deinit();
        try std.testing.expectEqualSlices(search.Match, &.{.{ .doc_id = 0, .offset = prefix.len, .end = prefix.len + 4 }}, matches.matches);
    }
}

test "file-only条件はraw検索を保ち壊れたYAMLを解析しない" {
    const allocator = std.testing.allocator;
    const documents = [_]search.Document{
        .{ .path = "/kb/a/note.md", .text = "---\ntitle: header-word\n---\nbody" },
        .{ .path = "/kb/b/note.md", .text = "---\nbroken header-word" },
        .{ .path = "/kb/other.md", .text = "header-word" },
    };
    var index = try search.Index.initMode(allocator, &documents, .normalized);
    defer index.deinit();
    var filter = try frontmatter.Filter.init(allocator, "file:/name", "\"note.md\"");
    defer filter.deinit(allocator);
    const request: Request = .{ .mode = .normalized, .terms = .{ .single = "HEADER-WORD" } };
    for ([_]bool{ false, true }) |scan| {
        var matches = try execute(allocator, &index, request, &.{filter}, if (scan) .scan else .index, null);
        defer matches.deinit();
        try std.testing.expectEqualSlices(search.Match, &.{ .{ .doc_id = 0, .offset = 11, .end = 22 }, .{ .doc_id = 1, .offset = 11, .end = 22 } }, matches.matches);
    }
    var unnormalized = try frontmatter.Filter.init(allocator, "file:/name", "\"NOTE.md\"");
    defer unnormalized.deinit(allocator);
    var no_match = try execute(allocator, &index, request, &.{unnormalized}, .index, null);
    defer no_match.deinit();
    try std.testing.expectEqual(@as(usize, 0), no_match.matches.len);
}

test "builtin pathはv2保存snapshotを使い相対pathも推測で書き換えない" {
    const storage = @import("storage.zig");
    const allocator = std.testing.allocator;
    var path = "relative/old.md".*;
    const documents = [_]search.Document{.{ .path = &path, .text = "saved body" }};
    var index = try search.Index.init(allocator, &documents);
    defer index.deinit();
    const encoded = try storage.encode(allocator, &index);
    defer allocator.free(encoded);
    @memcpy(&path, "relative/new.md");
    var stored = try storage.decode(allocator, encoded);
    defer stored.deinit();
    var old = try frontmatter.Filter.init(allocator, "file:/path", "\"relative/old.md\"");
    defer old.deinit(allocator);
    var changed = try frontmatter.Filter.init(allocator, "file:/path", "\"relative/new.md\"");
    defer changed.deinit(allocator);
    const request: Request = .{ .terms = .{ .single = "body" } };
    var found = try execute(allocator, &stored.index, request, &.{old}, .index, null);
    defer found.deinit();
    try std.testing.expectEqual(@as(usize, 1), found.matches.len);
    var absent = try execute(allocator, &stored.index, request, &.{changed}, .index, null);
    defer absent.deinit();
    try std.testing.expectEqual(@as(usize, 0), absent.matches.len);
}

fn exerciseResult(allocator: Allocator, index: *const search.Index, body_start: usize) !void {
    var filter = try frontmatter.Filter.init(allocator, "/state", "\"ready\"");
    defer filter.deinit(allocator);
    for ([_]Source{ .index, .scan }) |source| {
        for ([_]search.Mode{ .literal, .normalized }) |mode| {
            for ([_]ranking.Order{ .path, .heading, .relevance }) |order| {
                for ([_]Terms{ .{ .single = "ガラス" }, .{ .all = &.{ "ガラス", "断熱" } } }) |terms| {
                    var result = try execute(allocator, index, .{
                        .terms = terms,
                        .mode = mode,
                        .frontmatter = true,
                        .rank = order,
                        .relevance_settings = .{ .fields = &.{ .{ .kind = .body }, .{ .kind = .heading, .weight = 2 }, .{ .kind = .metadata, .pointer = "/subject" } } },
                    }, &.{filter}, source, null);
                    defer result.deinit();
                    try std.testing.expectEqual(@as(usize, 2), result.matches.len);
                    try std.testing.expectEqual(@as(usize, if (order == .path) 0 else 1), result.matches[0].doc_id);
                    for (result.matches) |match| {
                        try std.testing.expectEqual(body_start + @as(usize, if (match.doc_id == 0) 0 else 2), match.offset);
                        try std.testing.expectEqual(match.offset + "ガラス".len, match.end);
                    }
                    if (order == .relevance) {
                        try std.testing.expectEqual(result.matches.len, result.scores.?.len);
                        for (result.matches, result.scores.?) |match, score| {
                            try std.testing.expectEqual(match.doc_id, score.doc_id);
                            try std.testing.expectEqual(match.offset, score.offset);
                            try std.testing.expectEqual(match.end, score.end);
                        }
                    } else try std.testing.expectEqual(null, result.scores);
                }
            }
        }
    }
}

test "検索結果と説明を一括解放し投影・語句準備・採点の確保失敗を戻す" {
    const prefix = "---\nsubject: ガラス断熱\nstate: ready\n---\n";
    const documents = [_]search.Document{
        .{ .path = "a.md", .text = prefix ++ "ガラス\n断熱" },
        .{ .path = "b.md", .text = prefix ++ "# ガラス断熱" },
    };
    var index = try search.Index.initMode(std.testing.allocator, &documents, .normalized);
    defer index.deinit();
    try exerciseResult(std.testing.allocator, &index, prefix.len);
    try std.testing.checkAllAllocationFailures(std.testing.allocator, exerciseResult, .{ &index, prefix.len });
}

test "relevanceの空結果も内訳を所有し診断を初期化する" {
    const index: search.Index = .{ .allocator = std.testing.allocator, .documents = &.{}, .postings = &.{} };
    var diagnostic: ?frontmatter.Diagnostic = .{ .document_index = 42, .context = .frontmatter, .reason = error.InvalidFrontmatter };
    var result = try execute(std.testing.allocator, &index, .{ .terms = .{ .single = "不存在" }, .rank = .relevance }, &.{}, .scan, &diagnostic);
    defer result.deinit();
    try std.testing.expectEqual(@as(usize, 0), result.matches.len);
    try std.testing.expectEqual(@as(usize, 0), result.scores.?.len);
    try std.testing.expectEqual(null, diagnostic);
}

test "後半の採点失敗では結果を返さず全配列を解放する" {
    const documents = [_]search.Document{
        .{ .path = "a.md", .text = "---\nx: valid\n---\na" },
        .{ .path = "b.md", .text = "---\nx: [\n---\na" },
    };
    const index: search.Index = .{ .allocator = std.testing.allocator, .documents = &documents, .postings = &.{} };
    var diagnostic: ?frontmatter.Diagnostic = null;
    try std.testing.expectError(error.InvalidFrontmatter, execute(std.testing.allocator, &index, .{
        .terms = .{ .single = "a" },
        .rank = .relevance,
        .relevance_settings = .{ .fields = &.{.{ .kind = .metadata, .pointer = "/x", .weight = 0 }} },
    }, &.{}, .scan, &diagnostic));
    try std.testing.expectEqual(@as(usize, 1), diagnostic.?.document_index);
    try std.testing.expectEqualStrings("---\nx: valid\n---\na", documents[0].text);
}
