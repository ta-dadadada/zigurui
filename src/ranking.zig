//! 一致済み文書の保存原文から見出し優先順を求める。検索集合・一致位置・保存形式は変更しない。
const std = @import("std");
const profile = @import("profile.zig");
const search = @import("search.zig");
const normalize = @import("normalize.zig");
const frontmatter = @import("frontmatter.zig");

pub const Order = enum { path, heading, relevance };
const Scored = struct { match: search.Match, heading: bool };

/// documentsとtermsを借用し、matchesの順序だけを変更する。途中の失敗ではmatchesを変更しない。
/// 一時メモリは関数内で解放する。見出し内にいずれかの語が一致すれば優先し、同点はパス順。
pub fn sort(allocator: std.mem.Allocator, documents: []const search.Document, matches: []search.Match, terms: []const []const u8, mode: search.Mode) !void {
    const previous = profile.enter(.ranking);
    defer profile.leave(previous);
    if (matches.len < 2) return;
    const queries = try allocator.alloc([]const u8, terms.len);
    defer allocator.free(queries);
    var initialized: usize = 0;
    defer for (queries[0..initialized]) |q| allocator.free(q);
    for (terms, 0..) |term, i| {
        if (mode == .normalized) {
            var normalized = try normalize.transform(allocator, term);
            defer normalized.deinit();
            queries[i] = try allocator.dupe(u8, normalized.text);
        } else queries[i] = try allocator.dupe(u8, term);
        initialized += 1;
    }
    const scored = try allocator.alloc(Scored, matches.len);
    defer allocator.free(scored);
    for (matches, scored) |match, *item| item.* = .{
        .match = match,
        .heading = try hasHeading(allocator, documents[match.doc_id].text, queries, mode),
    };
    std.mem.sort(Scored, scored, documents, struct {
        fn less(docs: []const search.Document, a: Scored, b: Scored) bool {
            if (a.heading != b.heading) return a.heading;
            return std.mem.lessThan(u8, docs[a.match.doc_id].path, docs[b.match.doc_id].path);
        }
    }.less);
    for (scored, matches) |item, *match| match.* = item.match;
}

fn hasHeading(allocator: std.mem.Allocator, text: []const u8, queries: []const []const u8, mode: search.Mode) !bool {
    var values: std.ArrayList([]const u8) = .empty;
    defer values.deinit(allocator);
    try headings(allocator, text, &values);
    for (values.items) |heading| {
        if (mode == .normalized) {
            var normalized = try normalize.transform(allocator, heading);
            defer normalized.deinit();
            if (containsAny(normalized.text, queries)) return true;
        } else if (containsAny(heading, queries)) return true;
    }
    return false;
}

/// 既存heading契約のATX値をtextから借用してoutへ追加する。outの確保は呼び出し元が解放する。
pub fn headings(allocator: std.mem.Allocator, text: []const u8, out: *std.ArrayList([]const u8)) !void {
    // 壊れた/上限超過headerは加点しない。通常検索にYAML検証エラーを追加しない。
    const body_start = frontmatter.bodyStart(text) catch return;
    const start = if (body_start == 0 and std.mem.startsWith(u8, text, "\xef\xbb\xbf")) 3 else body_start;
    var lines = std.mem.splitScalar(u8, text[start..], '\n');
    var fence: u8 = 0;
    var fence_len: usize = 0;
    while (lines.next()) |raw| {
        const line = std.mem.trimEnd(u8, raw, "\r");
        var indent: usize = 0;
        while (indent < line.len and line[indent] == ' ') : (indent += 1) {}
        if (indent > 3 or indent == line.len) continue;
        const tail = line[indent..];
        if (tail[0] == '`' or tail[0] == '~') {
            var n: usize = 0;
            while (n < tail.len and tail[n] == tail[0]) : (n += 1) {}
            if (fence != 0) {
                if (tail[0] == fence and n >= fence_len and std.mem.trim(u8, tail[n..], " \t").len == 0) fence = 0;
            } else if (n >= 3 and (tail[0] != '`' or std.mem.indexOfScalar(u8, tail[n..], '`') == null)) {
                fence = tail[0];
                fence_len = n;
            }
            continue;
        }
        // 初版は字下げのないATXだけ。引用・リスト・インライン構文は解釈しない。
        if (fence != 0 or indent != 0 or tail[0] != '#') continue;
        var marks: usize = 0;
        while (marks < tail.len and tail[marks] == '#') : (marks += 1) {}
        if (marks > 6 or marks == tail.len or (tail[marks] != ' ' and tail[marks] != '\t')) continue;
        var heading = std.mem.trim(u8, tail[marks..], " \t");
        var end = heading.len;
        while (end > 0 and heading[end - 1] == '#') : (end -= 1) {}
        if (end == 0 or (end < heading.len and (heading[end - 1] == ' ' or heading[end - 1] == '\t'))) heading = std.mem.trimEnd(u8, heading[0..end], " \t");
        try out.append(allocator, heading);
    }
}

fn containsAny(text: []const u8, queries: []const []const u8) bool {
    for (queries) |query| if (query.len > 0 and std.mem.indexOf(u8, text, query) != null) return true;
    return false;
}

test "見出しの範囲と正規化" {
    const a = std.testing.allocator;
    try std.testing.expect(try hasHeading(a, "\xef\xbb\xbf# ガラス\n", &.{"ガラス"}, .literal));
    try std.testing.expect(try hasHeading(a, "# ｶﾞﾗｽ\r\n", &.{"ガラス"}, .normalized));
    try std.testing.expect(!try hasHeading(a, "# ｶﾞﾗｽ\n", &.{"ガラス"}, .literal));
    for ([_][]const u8{
        "---\n# ガラス\n---\n本文",
        "---\n# ガラス",
        "```md\n# ガラス\n```",
        "~~~\n# ガラス\n~~~",
        "> # ガラス",
        "- # ガラス",
        "  # ガラス",
        "####### ガラス",
        "#ガラス",
        "ガラス\n===",
        "# 見出し ###\nガラス",
    }) |text| try std.testing.expect(!try hasHeading(a, text, &.{"ガラス"}, .literal));
    try std.testing.expect(try hasHeading(a, "```\n# 無効\n```\n###### ガラス ###", &.{"ガラス"}, .literal));
}

fn exerciseSort(a: std.mem.Allocator) !void {
    const docs = [_]search.Document{
        .{ .path = "a", .text = "ガラス 断熱" },
        .{ .path = "b", .text = "断熱\n# ガラス" },
        .{ .path = "c", .text = "# 断熱\nガラス" },
    };
    var matches = [_]search.Match{ .{ .doc_id = 0, .offset = 0, .end = 9 }, .{ .doc_id = 1, .offset = 10, .end = 19 }, .{ .doc_id = 2, .offset = 12, .end = 21 } };
    const before = matches;
    sort(a, &docs, &matches, &.{ "ガラス", "断熱" }, .normalized) catch |err| {
        try std.testing.expectEqualDeep(before, matches);
        return err;
    };
    try std.testing.expectEqual(@as(usize, 1), matches[0].doc_id);
    try std.testing.expectEqual(@as(usize, 10), matches[0].offset);
    try std.testing.expectEqual(@as(usize, 19), matches[0].end);
    try std.testing.expectEqual(@as(usize, 2), matches[1].doc_id);
    try std.testing.expectEqual(@as(usize, 0), matches[2].doc_id);
}

test "同点パス順と位置維持・確保失敗" {
    try exerciseSort(std.testing.allocator);
    try std.testing.checkAllAllocationFailures(std.testing.allocator, exerciseSort, .{});
}
