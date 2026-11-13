//! CLIの引数処理、Markdownの収集、索引の保存と検索結果の表示を担当する。
//! 収集した原文と表示用メモリはプロセスのarenaで管理する。読み込んだ索引はgpaで所有し、検索後に解放する。

const std = @import("std");
const search = @import("search.zig");
const storage = @import("storage.zig");

const max_index_bytes = 1024 * 1024 * 1024;

const help =
    \\zigurui - ローカルMarkdown検索
    \\
    \\  zigurui index <directory> <index-file>
    \\  zigurui search <index-file> <query>
    \\  zigurui scan <directory> <query>
    \\  zigurui help
    \\
    \\queryは大文字・小文字を区別する部分一致。空白を含む場合は引用符で囲む。
    \\searchは保存時点の文書を検索する。変更後はindexを再実行する。
    \\索引ファイルにはMarkdown以外の拡張子を使用する。
    \\
;

/// index・search・scanを実行する。検索結果が0件でも正常終了し、失敗は標準エラーへ出力して終了コード1を返す。
pub fn main(init: std.process.Init) void {
    run(init) catch |err| {
        const message = std.fmt.allocPrint(init.arena.allocator(), "zigurui: {s}\n", .{@errorName(err)}) catch "zigurui: OutOfMemory\n";
        std.Io.File.stderr().writeStreamingAll(init.io, message) catch {};
        if (err == error.InvalidArguments) std.Io.File.stderr().writeStreamingAll(init.io, help) catch {};
        std.process.exit(1);
    };
}

fn run(init: std.process.Init) !void {
    const allocator = init.arena.allocator();
    const args = try init.minimal.args.toSlice(allocator);
    if (args.len == 1 or (args.len == 2 and (std.mem.eql(u8, args[1], "help") or std.mem.eql(u8, args[1], "--help")))) {
        try std.Io.File.stdout().writeStreamingAll(init.io, help);
        return;
    }
    if (args.len != 4) return error.InvalidArguments;
    if (std.mem.eql(u8, args[1], "index")) {
        // 文書への誤上書きと、索引自身の再取り込みを防ぐ。
        if (isMarkdown(args[3])) return error.IndexPathMustNotBeMarkdown;
        const documents = try loadCorpus(allocator, init.io, args[2]);
        // 索引は文書を借用するため、arena内の本文は検索・符号化が終わるまで保持する。
        var index = try search.Index.init(allocator, documents);
        defer index.deinit();
        const bytes = try storage.encode(allocator, &index);
        if (bytes.len >= max_index_bytes) return error.IndexTooLarge;
        // 書き込みと同期が成功してから置き換え、途中の失敗で既存索引を壊さない。
        var atomic = try std.Io.Dir.cwd().createFileAtomic(init.io, args[3], .{ .replace = true });
        defer atomic.deinit(init.io);
        try atomic.file.writeStreamingAll(init.io, bytes);
        try atomic.file.sync(init.io);
        try atomic.replace(init.io);
        const message = try std.fmt.allocPrint(allocator, "{d} documents indexed\n", .{documents.len});
        try std.Io.File.stdout().writeStreamingAll(init.io, message);
    } else if (std.mem.eql(u8, args[1], "search")) {
        const bytes = try std.Io.Dir.cwd().readFileAlloc(init.io, args[2], init.gpa, .limited(max_index_bytes));
        // 読み込みバッファを複製せず渡す。decodeOwnedは失敗時も所有権を引き取る。
        var stored = try storage.decodeOwned(init.gpa, bytes);
        defer stored.deinit();
        const matches = try stored.index.search(allocator, args[3]);
        try printMatches(allocator, init.io, stored.index.documents, matches);
    } else if (std.mem.eql(u8, args[1], "scan")) {
        const documents = try loadCorpus(allocator, init.io, args[2]);
        // 正解の基準となる全文走査では索引を構築しない。
        const index: search.Index = .{ .allocator = allocator, .documents = documents, .postings = &.{} };
        const matches = try index.scan(allocator, args[3]);
        try printMatches(allocator, init.io, documents, matches);
    } else return error.InvalidArguments;
}

fn isMarkdown(path: []const u8) bool {
    const extension = std.fs.path.extension(path);
    return std.ascii.eqlIgnoreCase(extension, ".md") or std.ascii.eqlIgnoreCase(extension, ".markdown");
}

// 文書配列・パス・本文と途中の確保は、呼び出し元のarenaで一括管理する。
// 原文は読み取り専用で開き、走査順に依存しない文書IDとなるようパス順に並べる。
fn loadCorpus(allocator: std.mem.Allocator, io: std.Io, root_path: []const u8) ![]const search.Document {
    const root = try std.Io.Dir.cwd().realPathFileAlloc(io, root_path, allocator);
    var dir = try std.Io.Dir.openDirAbsolute(io, root, .{ .iterate = true });
    defer dir.close(io);
    var walker = try dir.walk(allocator);
    defer walker.deinit();
    var documents: std.ArrayList(search.Document) = .empty;
    while (try walker.next(io)) |entry| {
        if (entry.kind != .file or !isMarkdown(entry.path)) continue;
        const path = try std.fs.path.join(allocator, &.{ root, entry.path });
        if (!std.unicode.utf8ValidateSlice(path)) return error.InvalidUtf8Path;
        const text = try entry.dir.readFileAlloc(io, entry.basename, allocator, .limited(64 * 1024 * 1024));
        if (!std.unicode.utf8ValidateSlice(text)) return error.InvalidUtf8;
        try documents.append(allocator, .{ .path = path, .text = text });
    }
    std.mem.sort(search.Document, documents.items, {}, struct {
        fn lessThan(_: void, a: search.Document, b: search.Document) bool {
            return std.mem.lessThan(u8, a.path, b.path);
        }
    }.lessThan);
    return documents.toOwnedSlice(allocator);
}

fn printMatches(allocator: std.mem.Allocator, io: std.Io, documents: []const search.Document, matches: []const search.Match) !void {
    for (matches) |match| {
        const doc = documents[match.doc_id];
        // Match.offsetはUTF-8のバイト位置。表示用の行番号は1から数える。
        const line = 1 + std.mem.countScalar(u8, doc.text[0..match.offset], '\n');
        const snippet = try excerpt(allocator, doc.text, match.offset);
        const output = try std.fmt.allocPrint(allocator, "{s}:{d}: {s}\n", .{ doc.path, line, snippet });
        try std.Io.File.stdout().writeStreamingAll(io, output);
    }
}

// textは検証済みUTF-8、offsetはそのコードポイント境界のバイト位置を受け取る。
// 抜粋の範囲はコードポイント数で制限し、返す複製は呼び出し元のallocatorが所有する。
fn excerpt(allocator: std.mem.Allocator, text: []const u8, offset: usize) ![]const u8 {
    var start = offset;
    var count: usize = 0;
    while (start > 0 and count < 32 and text[start - 1] != '\n') : (count += 1) {
        start -= 1;
        while (start > 0 and text[start] & 0xc0 == 0x80) start -= 1;
    }
    var end = offset;
    count = 0;
    while (end < text.len and count < 96 and text[end] != '\n') : (count += 1) {
        end += std.unicode.utf8ByteSequenceLength(text[end]) catch unreachable;
    }
    const snippet = try allocator.dupe(u8, text[start..end]);
    for (snippet) |*byte| if (byte.* < 0x20 or byte.* == 0x7f) {
        byte.* = ' ';
    };
    return snippet;
}

test "抜粋はUTF-8境界を保ち制御文字を除去する" {
    const text = "前置き\n検索\t日本語😀";
    const snippet = try excerpt(std.testing.allocator, text, std.mem.indexOf(u8, text, "日本").?);
    defer std.testing.allocator.free(snippet);
    try std.testing.expectEqualStrings("検索 日本語😀", snippet);
    try std.testing.expect(std.unicode.utf8ValidateSlice(snippet));
}

test {
    _ = search;
    _ = storage;
}
