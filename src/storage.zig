//! 文書のスナップショットと転置インデックスを独自形式で保存・復元する。
//! ファイル操作は呼び出し側が担い、ここではバイト列とメモリ所有権を扱う。

const std = @import("std");
const profile = @import("profile.zig");
const search = @import("search.zig");
const normalize = @import("normalize.zig");

/// 対応する保存形式の版。互換性のない変更時に更新する。
pub const format_version: u32 = 3;
const max_decoded_array_bytes = 1024 * 1024 * 1024;
const magic = "ZIGURUI\x00";
const header_size = magic.len + 4 + 8 + 8;

/// 復元に必要なメモリを所有する索引。呼び出し側はdeinitで一括解放する。
/// 文書とgramの文字列はbackingを借用し、posting配列とdoc_idsはindexが所有する。
/// indexだけを取り出して解放せず、この値を文字列の参照中は保持する。
pub const StoredIndex = struct {
    index: search.Index,
    backing: []u8,
    /// 保存バッファを借用するcanonical root。v2またはrootなしの内部索引はnull。
    source_root: ?[]const u8 = null,

    /// 索引、文書配列、借用先backingを順に解放する。呼び出し後の再利用はできない。
    pub fn deinit(self: *StoredIndex) void {
        const allocator = self.index.allocator;
        const documents = self.index.documents;
        self.index.deinit();
        allocator.free(documents);
        allocator.free(self.backing);
        self.* = undefined;
    }
};

/// 索引を借用し、文書とpostingをlittle endianの独自形式へ変換する。
/// 返却バイト列は呼び出し側が同じallocatorでfreeする。入力索引の所有権は移らない。
pub fn encode(allocator: std.mem.Allocator, index: *const search.Index) ![]u8 {
    return encodeWithRoot(allocator, index, null);
}

/// v3形式で保存する。source_rootは借用し、出力に複製する。
/// rootを指定する場合はcanonicalな絶対パスを呼び出し側が解決する。
pub fn encodeWithRoot(allocator: std.mem.Allocator, index: *const search.Index, source_root: ?[]const u8) ![]u8 {
    return encodeWithBudget(allocator, index, source_root, max_decoded_array_bytes);
}

fn encodeWithBudget(allocator: std.mem.Allocator, index: *const search.Index, source_root: ?[]const u8, limit: usize) ![]u8 {
    if (source_root) |root| if (!validRoot(root)) return error.InvalidIndex;
    // 読み込み側と同じ予算を保存前に検査し、次回読めない索引で旧版を置換しない。
    var budget: ArrayBudget = .{ .remaining = limit };
    try budget.consume(search.Document, index.documents.len);
    try budget.consume(search.Posting, index.postings.len);
    for (index.postings) |posting| try budget.consume(usize, posting.doc_ids.len);
    var output: std.ArrayList(u8) = .empty;
    defer output.deinit(allocator);
    try output.appendSlice(allocator, magic);
    try appendInt(&output, allocator, u32, format_version);
    try appendInt(&output, allocator, u64, 0);
    try appendInt(&output, allocator, u64, 0);
    try appendInt(&output, allocator, u64, index.documents.len);
    try appendInt(&output, allocator, u64, index.postings.len);
    try appendInt(&output, allocator, u32, @backingInt(index.mode));
    try appendInt(&output, allocator, u32, if (index.mode == .normalized) normalize.semantic_version else 0);
    try appendInt(&output, allocator, u32, if (index.mode == .normalized) normalize.unicode_version else 0);
    try appendBytes(&output, allocator, source_root orelse "");
    for (index.documents) |document| {
        try appendBytes(&output, allocator, document.path);
        try appendBytes(&output, allocator, document.text);
    }
    for (index.postings) |posting| {
        try appendBytes(&output, allocator, posting.gram);
        try appendInt(&output, allocator, u64, posting.doc_ids.len);
        var previous_id: usize = 0;
        for (posting.doc_ids, 0..) |id, i| {
            if (id >= index.documents.len or (i > 0 and id <= previous_id)) return error.InvalidIndex;
            try appendVarint(&output, allocator, id - previous_id);
            previous_id = id;
        }
    }
    putInt(u64, output.items[12..20], output.items.len - header_size);
    putInt(u64, output.items[20..28], checksum(output.items[header_size..]));
    return output.toOwnedSlice(allocator);
}

/// postingを再構築せず復元する。返却値は呼び出し側がStoredIndex.deinitで解放する。
/// 入力bytesを複製するため、呼び出し後に元のバイト列を破棄できる。
/// 破損・不正な構造はInvalidIndex、未対応の版はUnsupportedVersionを返す。
/// チェックサムは偶発的な破損を検出するもので、改ざんの認証は行わない。
pub fn decode(allocator: std.mem.Allocator, bytes: []const u8) !StoredIndex {
    return decodeOwned(allocator, try allocator.dupe(u8, bytes));
}

/// 同じallocatorで確保したbytesを所有権ごと受け取り、複製せず復元する。
/// 成功・失敗のいずれでも入力を消費するため、呼び出し側はbytesを解放・再利用しない。
/// 成功後はStoredIndex.deinitが入力全体を解放する。検証条件とエラーはdecodeと同じ。
pub fn decodeOwned(allocator: std.mem.Allocator, bytes: []u8) !StoredIndex {
    const previous = profile.enter(.decode);
    defer profile.leave(previous);
    errdefer allocator.free(bytes);
    var header: Reader = .{ .bytes = bytes };
    if (!std.mem.eql(u8, try header.take(magic.len), magic)) return error.InvalidIndex;
    const version = try header.int(u32);
    if (version != 2 and version != format_version) return error.UnsupportedVersion;
    const payload_len = try header.length();
    const expected_checksum = try header.int(u64);
    if (payload_len != bytes.len - header_size) return error.InvalidIndex;
    {
        const before_checksum = profile.enter(.checksum);
        defer profile.leave(before_checksum);
        if (checksum(bytes[header_size..]) != expected_checksum) return error.InvalidIndex;
    }

    var reader: Reader = .{ .bytes = bytes[header_size..] };
    const document_count = try reader.length();
    const posting_count = try reader.length();
    const mode: search.Mode = switch (try reader.int(u32)) {
        0 => .literal,
        1 => .normalized,
        else => return error.UnsupportedSearchProfile,
    };
    const semantic_version = try reader.int(u32);
    const unicode_version = try reader.int(u32);
    if (semantic_version != (if (mode == .normalized) normalize.semantic_version else @as(u32, 0)) or
        unicode_version != (if (mode == .normalized) normalize.unicode_version else @as(u32, 0))) return error.UnsupportedSearchProfile;
    const root = if (version == 3) try reader.string() else "";
    if (root.len != 0 and !validRoot(root)) return error.InvalidIndex;
    // 宣言件数による過大な確保を避ける。各文書は長さ2個で最低16バイト必要。
    if (document_count > reader.remaining() / 16) return error.InvalidIndex;
    // v2の配列は符号化サイズの2倍未満。従来の1GiB未満の索引を
    // 新しいv3の1GiB配列予算だけを理由に拒否しない。
    var budget: ArrayBudget = .{ .remaining = if (version == 2) 2 * max_decoded_array_bytes else max_decoded_array_bytes };
    budget.consume(search.Document, document_count) catch return error.InvalidIndex;
    // 文書文字列も確保前に検査する。長さ宣言だけで大きい配列を作らない。
    var document_probe = reader;
    for (0..document_count) |_| {
        const path = try document_probe.string();
        const text = try document_probe.string();
        if (path.len == 0 or std.mem.indexOfScalar(u8, path, 0) != null or
            !std.unicode.utf8ValidateSlice(path) or !std.unicode.utf8ValidateSlice(text)) return error.InvalidIndex;
    }
    const documents = try allocator.alloc(search.Document, document_count);
    errdefer allocator.free(documents);
    for (documents) |*document| {
        document.path = try reader.string();
        document.text = try reader.string();
        if (document.path.len == 0 or std.mem.indexOfScalar(u8, document.path, 0) != null or
            !std.unicode.utf8ValidateSlice(document.path) or !std.unicode.utf8ValidateSlice(document.text)) return error.InvalidIndex;
    }
    // 長さ2個と2バイト以上のgramに、v3はID1バイト以上、v2は8バイト必要。
    const minimum_posting: usize = if (version == 3) 19 else 26;
    if (posting_count > reader.remaining() / minimum_posting) return error.InvalidIndex;
    budget.consume(search.Posting, posting_count) catch return error.InvalidIndex;
    const postings = try allocator.alloc(search.Posting, posting_count);
    var initialized: usize = 0;
    errdefer {
        for (postings[0..initialized]) |posting| allocator.free(posting.doc_ids);
        allocator.free(postings);
    }
    for (postings, 0..) |*posting, i| {
        const gram = try reader.string();
        if ((std.unicode.utf8CountCodepoints(gram) catch return error.InvalidIndex) != 2) return error.InvalidIndex;
        if (i > 0 and std.mem.order(u8, postings[i - 1].gram, gram) != .lt) return error.InvalidIndex;
        const id_count = try reader.length();
        const minimum_id: usize = if (version == 3) 1 else 8;
        if (id_count == 0 or id_count > document_count or id_count > reader.remaining() / minimum_id) return error.InvalidIndex;
        budget.consume(usize, id_count) catch return error.InvalidIndex;
        // 確保前に符号化全体を検査し、短い破損データによる過大確保を防ぐ。
        var probe = reader;
        var previous_id: usize = 0;
        for (0..id_count) |j| previous_id = try readId(&probe, version, previous_id, j, document_count);
        const ids = try allocator.alloc(usize, id_count);
        posting.* = .{ .gram = gram, .doc_ids = ids };
        initialized += 1;
        for (ids, 0..) |*id, j| {
            id.* = try readId(&reader, version, if (j == 0) 0 else ids[j - 1], j, document_count);
        }
    }
    if (reader.remaining() != 0) return error.InvalidIndex;
    return .{
        .index = .{ .allocator = allocator, .documents = documents, .postings = postings, .mode = mode },
        .backing = bytes,
        .source_root = if (root.len == 0) null else root,
    };
}

// 復元後に所有する配列の合計。保存側と読取側が同じ境界を適用する。
const ArrayBudget = struct {
    remaining: usize,

    fn consume(self: *ArrayBudget, comptime T: type, count: usize) !void {
        if (count > self.remaining / @sizeOf(T)) return error.IndexTooLarge;
        self.remaining -= count * @sizeOf(T);
    }
};

fn validRoot(root: []const u8) bool {
    return root.len != 0 and std.fs.path.isAbsolute(root) and std.mem.indexOfScalar(u8, root, 0) == null and std.unicode.utf8ValidateSlice(root);
}

fn readId(reader: *Reader, version: u32, previous: usize, position: usize, count: usize) !usize {
    const value = if (version == 3) try reader.varint() else try reader.length();
    const id = if (version == 3) blk: {
        if (position > 0 and value == 0) return error.InvalidIndex;
        break :blk std.math.add(usize, previous, value) catch return error.InvalidIndex;
    } else value;
    if (id >= count or (position > 0 and id <= previous)) return error.InvalidIndex;
    return id;
}

fn appendVarint(output: *std.ArrayList(u8), allocator: std.mem.Allocator, input: usize) !void {
    var value = input;
    while (value >= 128) : (value >>= 7) try output.append(allocator, @as(u8, @truncate(value & 127)) | 128);
    try output.append(allocator, @intCast(value));
}

fn appendInt(output: *std.ArrayList(u8), allocator: std.mem.Allocator, comptime T: type, value: T) !void {
    var bytes: [@sizeOf(T)]u8 = undefined;
    putInt(T, &bytes, value);
    try output.appendSlice(allocator, &bytes);
}

fn putInt(comptime T: type, bytes: []u8, value: T) void {
    for (0..@sizeOf(T)) |i| bytes[i] = @truncate(value >> @as(std.math.Log2Int(T), @intCast(i * 8)));
}

fn appendBytes(output: *std.ArrayList(u8), allocator: std.mem.Allocator, bytes: []const u8) !void {
    try appendInt(output, allocator, u64, bytes.len);
    try output.appendSlice(allocator, bytes);
}

// FNV-1aは破損検出用であり、改ざんに対する認証ではない。
fn checksum(bytes: []const u8) u64 {
    var hash: u64 = 14695981039346656037;
    for (bytes) |byte| hash = (hash ^ byte) *% 1099511628211;
    return hash;
}

const Reader = struct {
    bytes: []const u8,
    offset: usize = 0,

    fn remaining(self: *const Reader) usize {
        return self.bytes.len - self.offset;
    }

    fn take(self: *Reader, count: usize) ![]const u8 {
        if (count > self.remaining()) return error.InvalidIndex;
        const result = self.bytes[self.offset..][0..count];
        self.offset += count;
        return result;
    }

    fn int(self: *Reader, comptime T: type) !T {
        const bytes = try self.take(@sizeOf(T));
        var value: T = 0;
        for (bytes, 0..) |byte, i| value |= @as(T, byte) << @as(std.math.Log2Int(T), @intCast(i * 8));
        return value;
    }

    fn varint(self: *Reader) !usize {
        var value: u64 = 0;
        for (0..10) |i| {
            const byte = (try self.take(1))[0];
            if (i == 9 and byte > 1) return error.InvalidIndex;
            value |= @as(u64, byte & 127) << @as(u6, @intCast(i * 7));
            if (byte & 128 == 0) {
                if (i > 0 and byte == 0) return error.InvalidIndex;
                return std.math.cast(usize, value) orelse error.InvalidIndex;
            }
        }
        return error.InvalidIndex;
    }

    fn length(self: *Reader) !usize {
        return std.math.cast(usize, try self.int(u64)) orelse error.InvalidIndex;
    }

    fn string(self: *Reader) ![]const u8 {
        return self.take(try self.length());
    }
};

const fixture = [_]search.Document{
    .{ .path = "日本語.md", .text = "検索エンジンとZig" },
    .{ .path = "nested/empty.md", .text = "" },
    .{ .path = "english.md", .text = "Zig search engine" },
};

test "保存したpostingを再構築せず復元し検索できる" {
    const allocator = std.testing.allocator;
    var index = try search.Index.init(allocator, &fixture);
    defer index.deinit();
    const bytes = try encode(allocator, &index);
    defer allocator.free(bytes);
    var stored = try decode(allocator, bytes);
    defer stored.deinit();
    try std.testing.expectEqual(index.documents.len, stored.index.documents.len);
    try std.testing.expectEqual(index.postings.len, stored.index.postings.len);
    for (index.documents, stored.index.documents) |expected, actual| {
        try std.testing.expectEqualStrings(expected.path, actual.path);
        try std.testing.expectEqualStrings(expected.text, actual.text);
    }
    for (index.postings, stored.index.postings) |expected, actual| {
        try std.testing.expectEqualStrings(expected.gram, actual.gram);
        try std.testing.expectEqualSlices(usize, expected.doc_ids, actual.doc_ids);
    }
    for ([_][]const u8{ "検索", "Zig", "検", "engine", "存在しない" }) |query| {
        const expected = try index.scan(allocator, query);
        defer allocator.free(expected);
        const actual = try stored.index.search(allocator, query);
        defer allocator.free(actual);
        try std.testing.expectEqualSlices(search.Match, expected, actual);
    }
}

test "切り詰め・破損・未対応version・末尾追加を拒否する" {
    const allocator = std.testing.allocator;
    var index = try search.Index.init(allocator, &fixture);
    defer index.deinit();
    const bytes = try encode(allocator, &index);
    defer allocator.free(bytes);
    for (0..bytes.len) |len| {
        try std.testing.expectError(error.InvalidIndex, decode(allocator, bytes[0..len]));
        try std.testing.expectError(error.InvalidIndex, decodeOwned(allocator, try allocator.dupe(u8, bytes[0..len])));
    }
    bytes[bytes.len - 1] ^= 1;
    try std.testing.expectError(error.InvalidIndex, decode(allocator, bytes));
    try std.testing.expectError(error.InvalidIndex, decodeOwned(allocator, try allocator.dupe(u8, bytes)));
    bytes[bytes.len - 1] ^= 1;
    bytes[0] ^= 1;
    try std.testing.expectError(error.InvalidIndex, decodeOwned(allocator, try allocator.dupe(u8, bytes)));
    bytes[0] ^= 1;
    bytes[8] = 4;
    try std.testing.expectError(error.UnsupportedVersion, decode(allocator, bytes));
    try std.testing.expectError(error.UnsupportedVersion, decodeOwned(allocator, try allocator.dupe(u8, bytes)));
    bytes[8] = 3;
    const appended = try allocator.alloc(u8, bytes.len + 1);
    defer allocator.free(appended);
    @memcpy(appended[0..bytes.len], bytes);
    appended[bytes.len] = 0;
    try std.testing.expectError(error.InvalidIndex, decode(allocator, appended));
    try std.testing.expectError(error.InvalidIndex, decodeOwned(allocator, try allocator.dupe(u8, appended)));
}

test "空の索引を保存できる" {
    const allocator = std.testing.allocator;
    var index = try search.Index.init(allocator, &.{});
    defer index.deinit();
    const bytes = try encode(allocator, &index);
    defer allocator.free(bytes);
    var stored = try decode(allocator, bytes);
    defer stored.deinit();
    try std.testing.expectEqual(@as(usize, 0), stored.index.documents.len);
}

fn expectInvalid(documents: []const search.Document, postings: []const search.Posting) !void {
    const allocator = std.testing.allocator;
    const index: search.Index = .{ .allocator = allocator, .documents = documents, .postings = postings };
    const bytes = try encodeLegacy(allocator, &index);
    defer allocator.free(bytes);
    try std.testing.expectError(error.InvalidIndex, decode(allocator, bytes));
    try std.testing.expectError(error.InvalidIndex, decodeOwned(allocator, try allocator.dupe(u8, bytes)));
}

test "checksumが正しくても不正なgram・文書ID・順序を拒否する" {
    try expectInvalid(&fixture, &.{.{ .gram = "検", .doc_ids = &.{0} }});
    try expectInvalid(&fixture, &.{.{ .gram = "\xff\xff", .doc_ids = &.{0} }});
    try expectInvalid(&fixture, &.{.{ .gram = "検索", .doc_ids = &.{3} }});
    try expectInvalid(&fixture, &.{.{ .gram = "検索", .doc_ids = &.{ 0, 0 } }});
    try expectInvalid(&fixture, &.{.{ .gram = "検索", .doc_ids = &.{ 2, 0 } }});
    try expectInvalid(&fixture, &.{.{ .gram = "検索", .doc_ids = &.{} }});
    try expectInvalid(&fixture, &.{
        .{ .gram = "検索", .doc_ids = &.{0} },
        .{ .gram = "検索", .doc_ids = &.{0} },
    });
    try expectInvalid(&fixture, &.{
        .{ .gram = "検索", .doc_ids = &.{0} },
        .{ .gram = "Zi", .doc_ids = &.{0} },
    });
    try expectInvalid(&.{.{ .path = "bad\x00.md", .text = "abc" }}, &.{});
    try expectInvalid(&.{.{ .path = "bad.md", .text = "\xff" }}, &.{});
}

test "checksumが正しくても過大な件数や文字列長を拒否する" {
    const allocator = std.testing.allocator;
    var index = try search.Index.init(allocator, &fixture);
    defer index.deinit();
    const original = try encode(allocator, &index);
    defer allocator.free(original);
    for ([_]usize{ header_size, header_size + 8, header_size + 28 }) |offset| {
        const bytes = try allocator.dupe(u8, original);
        defer allocator.free(bytes);
        putInt(u64, bytes[offset..][0..8], std.math.maxInt(u64));
        putInt(u64, bytes[20..28], checksum(bytes[header_size..]));
        try std.testing.expectError(error.InvalidIndex, decode(allocator, bytes));
        try std.testing.expectError(error.InvalidIndex, decodeOwned(allocator, try allocator.dupe(u8, bytes)));
    }
}

fn allocationRoundtrip(allocator: std.mem.Allocator) !void {
    var index = try search.Index.init(allocator, &fixture);
    defer index.deinit();
    const bytes = try encode(allocator, &index);
    defer allocator.free(bytes);
    var stored = try decode(allocator, bytes);
    defer stored.deinit();
}

test "allocation失敗時も構築・復元途中の所有メモリを解放する" {
    try std.testing.checkAllAllocationFailures(std.testing.allocator, allocationRoundtrip, .{});
}

test "所有入力は複製せず参照し、借用入力は復元後に変更できる" {
    const allocator = std.testing.allocator;
    var index = try search.Index.init(allocator, &fixture);
    defer index.deinit();
    const owned = try encode(allocator, &index);
    const owned_ptr = owned.ptr;
    const owned_len = owned.len;
    var stored_owned = try decodeOwned(allocator, owned);
    defer stored_owned.deinit();
    try std.testing.expectEqual(owned_ptr, stored_owned.backing.ptr);
    try std.testing.expectEqual(owned_len, stored_owned.backing.len);

    const borrowed = try encode(allocator, &index);
    defer allocator.free(borrowed);
    var stored_borrowed = try decode(allocator, borrowed);
    defer stored_borrowed.deinit();
    try std.testing.expect(borrowed.ptr != stored_borrowed.backing.ptr);
    @memset(borrowed, 0);
    const matches = try stored_borrowed.index.search(allocator, "検索");
    defer allocator.free(matches);
    try std.testing.expectEqual(@as(usize, 1), matches.len);
    try std.testing.expectEqualStrings(fixture[0].text, stored_borrowed.index.documents[0].text);
}

fn ownedAllocationRoundtrip(allocator: std.mem.Allocator) !void {
    var index = try search.Index.init(allocator, &fixture);
    defer index.deinit();
    const bytes = try encode(allocator, &index);
    // decodeOwnedは失敗時もbytesを解放するため、ここではdefer freeしない。
    var stored = try decodeOwned(allocator, bytes);
    defer stored.deinit();
}

test "所有入力の復元がallocation失敗しても入力と途中配列を解放する" {
    try std.testing.checkAllAllocationFailures(std.testing.allocator, ownedAllocationRoundtrip, .{});
}

test "正規化profileの保存復元とliteral走査を維持する" {
    const allocator = std.testing.allocator;
    const docs = [_]search.Document{.{ .path = "a.md", .text = "前\nＡＢＣ ｶﾞ ㌔" }};
    var index = try search.Index.initMode(allocator, &docs, .normalized);
    defer index.deinit();
    const bytes = try encode(allocator, &index);
    defer allocator.free(bytes);
    var stored = try decode(allocator, bytes);
    defer stored.deinit();
    try std.testing.expectEqual(search.Mode.normalized, stored.index.mode);
    for ([_][]const u8{ "abc", "ＡＢＣ", "ｶﾞ", "ガ", "ロ", "㌔", "x" }) |query| {
        const expected = try index.scanMode(allocator, query, .normalized);
        defer allocator.free(expected);
        const actual = try stored.index.searchMode(allocator, query, .normalized);
        defer allocator.free(actual);
        try std.testing.expectEqualSlices(search.Match, expected, actual);
    }
    const literal = try stored.index.search(allocator, "abc");
    defer allocator.free(literal);
    try std.testing.expectEqual(@as(usize, 0), literal.len);
    for ([_]usize{ header_size + 16, header_size + 20, header_size + 24 }) |offset| {
        const bad = try allocator.dupe(u8, bytes);
        defer allocator.free(bad);
        putInt(u32, bad[offset..][0..4], 999);
        putInt(u64, bad[20..28], checksum(bad[header_size..]));
        try std.testing.expectError(error.UnsupportedSearchProfile, decode(allocator, bad));
    }
    bytes[8] = 1;
    try std.testing.expectError(error.UnsupportedVersion, decode(allocator, bytes));
}

// v2読取互換と不正構造を検証するためだけの旧形式fixture生成。
fn encodeLegacy(allocator: std.mem.Allocator, index: *const search.Index) ![]u8 {
    var output: std.ArrayList(u8) = .empty;
    defer output.deinit(allocator);
    try output.appendSlice(allocator, magic);
    try appendInt(&output, allocator, u32, 2);
    try appendInt(&output, allocator, u64, 0);
    try appendInt(&output, allocator, u64, 0);
    try appendInt(&output, allocator, u64, index.documents.len);
    try appendInt(&output, allocator, u64, index.postings.len);
    try appendInt(&output, allocator, u32, @backingInt(index.mode));
    try appendInt(&output, allocator, u32, if (index.mode == .normalized) normalize.semantic_version else 0);
    try appendInt(&output, allocator, u32, if (index.mode == .normalized) normalize.unicode_version else 0);
    for (index.documents) |document| {
        try appendBytes(&output, allocator, document.path);
        try appendBytes(&output, allocator, document.text);
    }
    for (index.postings) |posting| {
        try appendBytes(&output, allocator, posting.gram);
        try appendInt(&output, allocator, u64, posting.doc_ids.len);
        for (posting.doc_ids) |id| try appendInt(&output, allocator, u64, id);
    }
    putInt(u64, output.items[12..20], output.items.len - header_size);
    putInt(u64, output.items[20..28], checksum(output.items[header_size..]));
    return output.toOwnedSlice(allocator);
}

test "v2は検索可能でrootを持たずv3はrootを保存バッファから借用する" {
    const a = std.testing.allocator;
    var index = try search.Index.init(a, &fixture);
    defer index.deinit();
    const legacy = try encodeLegacy(a, &index);
    defer a.free(legacy);
    var old = try decode(a, legacy);
    defer old.deinit();
    try std.testing.expect(old.source_root == null);
    const bytes = try encodeWithRoot(a, &index, "/private/synthetic");
    defer a.free(bytes);
    var stored = try decode(a, bytes);
    defer stored.deinit();
    try std.testing.expectEqualStrings("/private/synthetic", stored.source_root.?);
    for (index.postings, stored.index.postings, old.index.postings) |expected, current, previous| {
        try std.testing.expectEqualSlices(usize, expected.doc_ids, current.doc_ids);
        try std.testing.expectEqualSlices(usize, expected.doc_ids, previous.doc_ids);
    }
    try std.testing.expectError(error.InvalidIndex, encodeWithRoot(a, &index, "relative"));
    try std.testing.expectError(error.InvalidIndex, encodeWithRoot(a, &index, "/bad\x00root"));
}

fn compressedFixture(allocator: std.mem.Allocator, ids: []const u8, count: u64) ![]u8 {
    const index: search.Index = .{ .allocator = allocator, .documents = &fixture, .postings = &.{.{ .gram = "ab", .doc_ids = &.{0} }} };
    const valid = try encode(allocator, &index);
    defer allocator.free(valid);
    const bytes = try allocator.alloc(u8, valid.len - 1 + ids.len);
    @memcpy(bytes[0 .. valid.len - 1], valid[0 .. valid.len - 1]);
    @memcpy(bytes[valid.len - 1 ..], ids);
    putInt(u64, bytes[valid.len - 9 ..][0..8], count);
    putInt(u64, bytes[12..20], bytes.len - header_size);
    putInt(u64, bytes[20..28], checksum(bytes[header_size..]));
    return bytes;
}

test "正しいchecksumでも過長varintとゼロ差分と件数異常を拒否する" {
    const a = std.testing.allocator;
    const cases = [_]struct { data: []const u8, count: u64 }{
        .{ .data = &.{ 0, 0 }, .count = 2 },
        .{ .data = &.{ 0, 128 }, .count = 2 },
        .{ .data = &.{ 128, 0 }, .count = 1 },
        .{ .data = &.{ 255, 255, 255, 255, 255, 255, 255, 255, 255, 2 }, .count = 1 },
        .{ .data = &.{255}, .count = 1 },
        .{ .data = &.{3}, .count = 1 },
        .{ .data = &.{0}, .count = 0 },
        .{ .data = &.{0}, .count = std.math.maxInt(u64) },
    };
    for (cases) |case| {
        const bytes = try compressedFixture(a, case.data, case.count);
        defer a.free(bytes);
        try std.testing.expectError(error.InvalidIndex, decode(a, bytes));
        try std.testing.expectError(error.InvalidIndex, decodeOwned(a, try a.dupe(u8, bytes)));
    }
    var overflow: Reader = .{ .bytes = &.{1} };
    try std.testing.expectError(error.InvalidIndex, readId(&overflow, 3, std.math.maxInt(usize), 1, std.math.maxInt(usize)));
}

test "varintの境界と疎なID列を復元する" {
    const a = std.testing.allocator;
    var output: std.ArrayList(u8) = .empty;
    defer output.deinit(a);
    const values = [_]usize{ 0, 1, 127, 128, 16383, 16384, std.math.maxInt(usize) };
    for (values) |value| try appendVarint(&output, a, value);
    var reader: Reader = .{ .bytes = output.items };
    for (values) |value| try std.testing.expectEqual(value, try reader.varint());
    try std.testing.expectEqual(@as(usize, 0), reader.remaining());
}

test "復元配列の容量制限を保存前にも共有し上限とoverflowを拒否する" {
    const a = std.testing.allocator;
    var index = try search.Index.init(a, &fixture);
    defer index.deinit();
    var required = index.documents.len * @sizeOf(search.Document) + index.postings.len * @sizeOf(search.Posting);
    for (index.postings) |posting| required += posting.doc_ids.len * @sizeOf(usize);
    // 予算超過は最初の出力確保より前に拒否する。
    try std.testing.expectError(error.IndexTooLarge, encodeWithBudget(std.testing.failing_allocator, &index, "/synthetic", required - 1));
    const bytes = try encodeWithBudget(a, &index, "/synthetic", required);
    defer a.free(bytes);
    var stored = try decode(a, bytes);
    defer stored.deinit();
    var budget: ArrayBudget = .{ .remaining = @sizeOf(usize) };
    try budget.consume(usize, 1);
    try std.testing.expectError(error.IndexTooLarge, budget.consume(usize, 1));
    try std.testing.expectError(error.IndexTooLarge, budget.consume(usize, std.math.maxInt(usize)));
}
