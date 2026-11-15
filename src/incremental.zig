//! 文書のパスと本文を比較し、未変更文書の転置一覧を再利用する。
//! ファイルの走査と原子的な保存は呼び出し側の責任とする。
const std = @import("std");
const search = @import("search.zig");
const Allocator = std.mem.Allocator;

/// 今回の文書集合に対する追加・変更・削除・未変更の件数。
pub const Changes = struct {
    added: usize = 0,
    modified: usize = 0,
    deleted: usize = 0,
    unchanged: usize = 0,
};

/// 全文書と順序が同一ならindexはnull。呼び出し側は検証済みの旧索引を維持する。
/// indexがある場合は現在のdocumentsを借用し、gramと転置一覧を所有する。
/// 呼び出し側がindex.deinitで解放する。previousからの借用は残さない。
pub const Result = struct { index: ?search.Index, changes: Changes };

/// 検証済みのpreviousと同じ検索モードで、現在の文書集合の索引を作る。
/// パスは完全一致で識別し、本文のバイト一致で未変更を判定する。
/// IDはdocumentsの添字へ付け直す。削除文書や旧IDを結果に残さない。
/// documentsの順序は呼び出し側が決める。重複パスは拒否する。
/// 成功・失敗のいずれでもpreviousとdocumentsは変更しない。
pub fn reconcile(allocator: Allocator, previous: *const search.Index, documents: []const search.Document) !Result {
    var old_paths = std.StringHashMap(usize).init(allocator);
    defer old_paths.deinit();
    for (previous.documents, 0..) |doc, id| {
        const entry = try old_paths.getOrPut(doc.path);
        if (entry.found_existing) return error.DuplicateDocumentPath;
        entry.value_ptr.* = id;
    }
    const mapping = try allocator.alloc(?usize, previous.documents.len);
    defer allocator.free(mapping);
    @memset(mapping, null);
    var seen = std.StringHashMap(void).init(allocator);
    defer seen.deinit();
    var changed: std.ArrayList(search.Document) = .empty;
    defer changed.deinit(allocator);
    var changed_ids: std.ArrayList(usize) = .empty;
    defer changed_ids.deinit(allocator);
    var changes: Changes = .{};
    var same_order = true;
    for (documents, 0..) |doc, id| {
        const entry = try seen.getOrPut(doc.path);
        if (entry.found_existing) return error.DuplicateDocumentPath;
        if (old_paths.get(doc.path)) |old_id| {
            if (std.mem.eql(u8, previous.documents[old_id].text, doc.text)) {
                mapping[old_id] = id;
                if (old_id != id) same_order = false;
                changes.unchanged += 1;
                continue;
            }
            changes.modified += 1;
        } else changes.added += 1;
        try changed.append(allocator, doc);
        try changed_ids.append(allocator, id);
    }
    changes.deleted = previous.documents.len - changes.unchanged - changes.modified;
    if (same_order and changes.added == 0 and changes.modified == 0 and changes.deleted == 0)
        return .{ .index = null, .changes = changes };
    var delta = try search.Index.initMode(allocator, changed.items, previous.mode);
    defer delta.deinit();
    var map = std.StringHashMap(std.ArrayList(usize)).init(allocator);
    var transferred = false;
    defer {
        if (!transferred) {
            var it = map.iterator();
            while (it.next()) |entry| {
                allocator.free(entry.key_ptr.*);
                entry.value_ptr.deinit(allocator);
            }
        }
        map.deinit();
    }
    for (previous.postings) |posting| {
        for (posting.doc_ids) |old_id| {
            if (mapping[old_id]) |id| try append(&map, allocator, posting.gram, id);
        }
    }
    for (delta.postings) |posting| {
        for (posting.doc_ids) |id| try append(&map, allocator, posting.gram, changed_ids.items[id]);
    }
    const postings = try allocator.alloc(search.Posting, map.count());
    errdefer allocator.free(postings);
    // toOwnedSliceは失敗し得るため、所有権を移さず配列を参照して組み立てる。
    var it = map.iterator();
    var position: usize = 0;
    while (it.next()) |entry| : (position += 1) {
        std.mem.sort(usize, entry.value_ptr.items, {}, std.sort.asc(usize));
        postings[position] = .{ .gram = entry.key_ptr.*, .doc_ids = entry.value_ptr.items };
    }
    std.mem.sort(search.Posting, postings, {}, lessThan);
    // ArrayListの容量と長さを揃えて、Index.deinitのslice解放契約を満たす。
    for (postings, 0..) |*posting, i| {
        const list = map.getPtr(posting.gram).?;
        posting.doc_ids = list.toOwnedSlice(allocator) catch |err| {
            for (postings[0..i]) |owned| allocator.free(owned.doc_ids);
            return err;
        };
    }
    transferred = true;
    return .{ .index = .{ .allocator = allocator, .documents = documents, .postings = postings, .mode = previous.mode, .owns_grams = true }, .changes = changes };
}

fn append(map: *std.StringHashMap(std.ArrayList(usize)), allocator: Allocator, gram: []const u8, id: usize) !void {
    const entry = try map.getOrPut(gram);
    if (!entry.found_existing) {
        entry.value_ptr.* = .empty;
        entry.key_ptr.* = allocator.dupe(u8, gram) catch |err| {
            _ = map.remove(gram);
            return err;
        };
    }
    try entry.value_ptr.append(allocator, id);
}

fn lessThan(_: void, left: search.Posting, right: search.Posting) bool {
    return std.mem.order(u8, left.gram, right.gram) == .lt;
}

const before = [_]search.Document{
    .{ .path = "/notes/a.md", .text = "# 記録\nｶﾞﾗｽと猫" },
    .{ .path = "/notes/b.md", .text = "# 古い本文\n窓の断熱" },
    .{ .path = "/notes/c.md", .text = "削除する文書" },
};
const after = [_]search.Document{
    .{ .path = "/notes/new.md", .text = "# 追加\n猫と断熱" },
    before[0],
    .{ .path = "/notes/b.md", .text = "# 新しい本文\nガラスと断熱" },
};

fn verify(allocator: Allocator, mode: search.Mode) !void {
    var old = try search.Index.initMode(allocator, &before, mode);
    defer old.deinit();
    var result = try reconcile(allocator, &old, &after);
    defer if (result.index) |*index| index.deinit();
    try std.testing.expectEqualDeep(Changes{ .added = 1, .modified = 1, .deleted = 1, .unchanged = 1 }, result.changes);
    var full = try search.Index.initMode(allocator, &after, mode);
    defer full.deinit();
    const storage = @import("storage.zig");
    const rebuilt = try storage.encode(allocator, &full);
    defer allocator.free(rebuilt);
    const updated = try storage.encode(allocator, &result.index.?);
    defer allocator.free(updated);
    try std.testing.expectEqualSlices(u8, rebuilt, updated);
    for ([_][]const u8{ "猫", "ガラス", "ｶﾞﾗｽ", "断熱", "削除", "不存在" }) |query| {
        const actual = try result.index.?.search(allocator, query);
        defer allocator.free(actual);
        const expected = try full.scan(allocator, query);
        defer allocator.free(expected);
        try std.testing.expectEqualSlices(search.Match, expected, actual);
    }
}

test "追加変更削除とID移動は両モードで全再構築と同一になる" {
    try verify(std.testing.allocator, .literal);
    try verify(std.testing.allocator, .normalized);
}

test "空集合への削除と同一内容での再実行を扱い重複パスを拒否する" {
    const a = std.testing.allocator;
    var old = try search.Index.init(a, &before);
    defer old.deinit();
    const same = try reconcile(a, &old, &before);
    try std.testing.expect(same.index == null);
    try std.testing.expectEqual(@as(usize, 3), same.changes.unchanged);
    var empty = try reconcile(a, &old, &.{});
    defer empty.index.?.deinit();
    try std.testing.expectEqual(@as(usize, 3), empty.changes.deleted);
    try std.testing.expectEqual(@as(usize, 0), empty.index.?.postings.len);
    var added = try reconcile(a, &empty.index.?, &before);
    defer added.index.?.deinit();
    try std.testing.expectEqual(@as(usize, 3), added.changes.added);
    try std.testing.expectError(error.DuplicateDocumentPath, reconcile(a, &old, &.{ before[0], before[0] }));
}

test "差分索引は旧索引の寿命に依存せず独立期待結果と一致する" {
    const a = std.testing.allocator;
    var old = try search.Index.initMode(a, &before, .normalized);
    var old_alive = true;
    defer if (old_alive) old.deinit();
    var result = try reconcile(a, &old, &after);
    defer if (result.index) |*index| index.deinit();
    old.deinit();
    old_alive = false;
    const cat = try result.index.?.search(a, "猫");
    defer a.free(cat);
    try std.testing.expectEqual(@as(usize, 2), cat.len);
    try std.testing.expectEqualStrings("/notes/new.md", after[cat[0].doc_id].path);
    try std.testing.expectEqualStrings("/notes/a.md", after[cat[1].doc_id].path);
    const all = try result.index.?.searchAll(a, &.{ "ガラス", "断熱" }, .normalized);
    defer a.free(all);
    try std.testing.expectEqual(@as(usize, 1), all.len);
    try std.testing.expectEqualStrings("/notes/b.md", after[all[0].doc_id].path);
    const removed = try result.index.?.search(a, "削除");
    defer a.free(removed);
    try std.testing.expectEqual(@as(usize, 0), removed.len);
}

fn reconcileFailure(allocator: Allocator, old: *const search.Index) !void {
    var result = try reconcile(allocator, old, &after);
    defer if (result.index) |*index| index.deinit();
}

test "確保失敗を繰り返しても旧索引の保存内容は変わらない" {
    const a = std.testing.allocator;
    const storage = @import("storage.zig");
    var old = try search.Index.initMode(a, &before, .normalized);
    defer old.deinit();
    const original = try storage.encode(a, &old);
    defer a.free(original);
    try std.testing.checkAllAllocationFailures(a, reconcileFailure, .{&old});
    const retained = try storage.encode(a, &old);
    defer a.free(retained);
    try std.testing.expectEqualSlices(u8, original, retained);
}

test "同じ集合でも順序が違う場合はIDを付け直し空集合の未変更では構築しない" {
    const a = std.testing.allocator;
    var old = try search.Index.init(a, &before);
    defer old.deinit();
    const reordered = [_]search.Document{ before[2], before[0], before[1] };
    var result = try reconcile(a, &old, &reordered);
    defer if (result.index) |*index| index.deinit();
    try std.testing.expect(result.index != null);
    try std.testing.expectEqual(@as(usize, 3), result.changes.unchanged);
    var full = try search.Index.init(a, &reordered);
    defer full.deinit();
    const storage = @import("storage.zig");
    const actual = try storage.encode(a, &result.index.?);
    defer a.free(actual);
    const expected = try storage.encode(a, &full);
    defer a.free(expected);
    try std.testing.expectEqualSlices(u8, expected, actual);
    var empty = try search.Index.init(a, &.{});
    defer empty.deinit();
    const unchanged = try reconcile(a, &empty, &.{});
    try std.testing.expect(unchanged.index == null);
    try std.testing.expectEqualDeep(Changes{}, unchanged.changes);
}

fn unchangedFailure(allocator: Allocator, old: *const search.Index) !void {
    const result = try reconcile(allocator, old, old.documents);
    try std.testing.expect(result.index == null);
}

test "変更なし判定中の確保失敗でも旧索引を変更せず解放漏れを残さない" {
    const a = std.testing.allocator;
    var old = try search.Index.init(a, &before);
    defer old.deinit();
    const storage = @import("storage.zig");
    const original = try storage.encode(a, &old);
    defer a.free(original);
    try std.testing.checkAllAllocationFailures(a, unchangedFailure, .{&old});
    const retained = try storage.encode(a, &old);
    defer a.free(retained);
    try std.testing.expectEqualSlices(u8, original, retained);
}
