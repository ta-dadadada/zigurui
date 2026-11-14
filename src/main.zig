//! CLIの引数処理、Markdownの収集、索引の保存と検索結果の表示を担当する。
//! 収集した原文と表示用メモリはプロセスのarenaで管理する。読み込んだ索引はgpaで所有し、検索後に解放する。

const std = @import("std");
const profile = @import("profile.zig");
const search = @import("search.zig");
const storage = @import("storage.zig");
const frontmatter = @import("frontmatter.zig");

const max_index_bytes = 1024 * 1024 * 1024;

const help =
    \\zigurui - ローカルMarkdown検索
    \\
    \\  zigurui index [--mode literal|normalized] <directory> <index-file>
    \\  zigurui search [--mode literal|normalized] <index-file> <query>
    \\  zigurui scan [--mode literal|normalized] <directory> <query>
    \\  zigurui search [--mode literal|normalized] --all <index-file> <term>...
    \\  zigurui scan [--mode literal|normalized] --all <directory> <term>...
    \\  zigurui schema <index-file>
    \\  search/scanの入力前: [--frontmatter] [--where <selector> <JSON-scalar>]...
    \\  zigurui help
    \\
    \\既定のliteralは原文の連続部分一致。normalizedはNFKC・full case foldingを使う。
    \\空白も検索語の一部。空白を含む場合は引用符で囲む。
    \\--allは指定した全語句を同一文書で検索する。順序は問わない。
    \\--frontmatterは本文だけを検索する。--whereはYAMLキーやfile:/name・file:/pathで絞る。
    \\normalized検索には同じmodeで構築した索引が必要。literalは両索引で使える。
    \\searchは保存時点の文書を検索する。変更後はindexを再実行する。
    \\索引ファイルにはMarkdown以外の拡張子を使用する。
    \\
;

/// index・search・scanを実行する。検索結果が0件でも正常終了し、失敗は標準エラーへ出力して終了コード1を返す。
pub fn main(init: std.process.Init) void {
    var reported = false;
    run(init, &reported) catch |err| {
        if (reported) std.process.exit(1);
        const hint: []const u8 = switch (err) {
            error.UnsupportedVersion, error.UnsupportedSearchProfile, error.IndexModeMismatch => " (index --modeで索引を再構築してください)",
            error.NormalizationTooLarge => " (非ASCIIの正規化は各中間表現4,194,304コードポイントまでです)",
            else => "",
        };
        const message = std.fmt.allocPrint(init.arena.allocator(), "zigurui: {s}{s}\n", .{ @errorName(err), hint }) catch "zigurui: OutOfMemory\n";
        std.Io.File.stderr().writeStreamingAll(init.io, message) catch {};
        if (err == error.InvalidArguments) std.Io.File.stderr().writeStreamingAll(init.io, help) catch {};
        std.process.exit(1);
    };
}

fn run(init: std.process.Init, reported: *bool) !void {
    var diagnostic: ?frontmatter.Diagnostic = null;
    const allocator = init.arena.allocator();
    const args = try init.minimal.args.toSlice(allocator);
    if (args.len == 1 or (args.len == 2 and (std.mem.eql(u8, args[1], "help") or std.mem.eql(u8, args[1], "--help")))) {
        try std.Io.File.stdout().writeStreamingAll(init.io, help);
        return;
    }
    const parsed = try parseArguments(args);
    profile.init(init.io);
    var succeeded = false;
    const command = if (std.mem.eql(u8, args[1], "search")) "search" else if (std.mem.eql(u8, args[1], "scan")) "scan" else if (std.mem.eql(u8, args[1], "index")) "index" else if (std.mem.eql(u8, args[1], "schema")) "schema" else "unknown";
    defer profile.report(init.io, command, @tagName(parsed.mode), succeeded);
    const filters = try prepareFilters(init.gpa, &parsed);
    defer {
        for (filters) |*filter| filter.deinit(init.gpa);
        init.gpa.free(filters);
    }
    const mode = parsed.mode;
    const input = parsed.input;
    const value = parsed.value;
    if (std.mem.eql(u8, args[1], "index")) {
        // 文書への誤上書きと、索引自身の再取り込みを防ぐ。
        if (isMarkdown(value)) return error.IndexPathMustNotBeMarkdown;
        const documents = try loadCorpus(allocator, init.io, input);
        // 索引は文書を借用するため、arena内の本文は検索・符号化が終わるまで保持する。
        var index = try search.Index.initMode(init.gpa, documents, mode);
        defer index.deinit();
        const bytes = try storage.encode(allocator, &index);
        if (bytes.len >= max_index_bytes) return error.IndexTooLarge;
        // 書き込みと同期が成功してから置き換え、途中の失敗で既存索引を壊さない。
        var atomic = try std.Io.Dir.cwd().createFileAtomic(init.io, value, .{ .replace = true });
        defer atomic.deinit(init.io);
        try atomic.file.writeStreamingAll(init.io, bytes);
        try atomic.file.sync(init.io);
        try atomic.replace(init.io);
        const message = try std.fmt.allocPrint(allocator, "{d} documents indexed\n", .{documents.len});
        try std.Io.File.stdout().writeStreamingAll(init.io, message);
    } else if (std.mem.eql(u8, args[1], "search") or std.mem.eql(u8, args[1], "schema")) {
        const bytes = blk: {
            const previous = profile.enter(.read);
            defer profile.leave(previous);
            break :blk try std.Io.Dir.cwd().readFileAlloc(init.io, input, init.gpa, .limited(max_index_bytes));
        };
        // 読み込みバッファを複製せず渡す。decodeOwnedは失敗時も所有権を引き取る。
        var stored = try storage.decodeOwned(init.gpa, bytes);
        defer {
            const previous = profile.enter(.cleanup);
            defer profile.leave(previous);
            stored.deinit();
        }
        if (std.mem.eql(u8, args[1], "schema")) {
            const json = frontmatter.schemaJson(init.gpa, stored.index.documents, &diagnostic) catch |err| {
                reportDiagnostic(init.io, stored.index.documents, diagnostic, reported);
                return err;
            };
            defer init.gpa.free(json);
            const previous = profile.enter(.output);
            defer profile.leave(previous);
            try std.Io.File.stdout().writeStreamingAll(init.io, json);
            try std.Io.File.stdout().writeStreamingAll(init.io, "\n");
            succeeded = true;
            return;
        }
        const matches = findMatches(init.gpa, &stored.index, &parsed, filters, false, &diagnostic) catch |err| {
            reportDiagnostic(init.io, stored.index.documents, diagnostic, reported);
            return err;
        };
        defer {
            const previous = profile.enter(.cleanup);
            defer profile.leave(previous);
            init.gpa.free(matches);
        }
        try printMatches(allocator, init.io, stored.index.documents, matches);
    } else if (std.mem.eql(u8, args[1], "scan")) {
        const documents = try loadCorpus(allocator, init.io, input);
        // 正解の基準となる全文走査では索引を構築しない。
        const index: search.Index = .{ .allocator = allocator, .documents = documents, .postings = &.{} };
        const matches = findMatches(init.gpa, &index, &parsed, filters, true, &diagnostic) catch |err| {
            reportDiagnostic(init.io, documents, diagnostic, reported);
            return err;
        };
        defer {
            const previous = profile.enter(.cleanup);
            defer profile.leave(previous);
            init.gpa.free(matches);
        }
        try printMatches(allocator, init.io, documents, matches);
    } else return error.InvalidArguments;
    succeeded = true;
}

const Where = struct { pointer: []const u8, json: []const u8 };
const Arguments = struct {
    mode: search.Mode,
    input: []const u8,
    value: []const u8 = "",
    all: bool = false,
    terms: []const []const u8 = &.{},
    frontmatter: bool = false,
    where: [frontmatter.max_filters]Where = undefined,
    where_count: usize = 0,
};

fn parseArguments(args: []const []const u8) !Arguments {
    if (args.len < 2) return error.InvalidArguments;
    if (std.mem.eql(u8, args[1], "schema")) {
        if (args.len != 3) return error.InvalidArguments;
        return .{ .mode = .literal, .input = args[2] };
    }
    // 従来の入力名--modeは維持し、新しい明示オプションだけを予約する。
    if (args.len == 4 and !std.mem.eql(u8, args[2], "--all") and !std.mem.eql(u8, args[2], "--frontmatter") and !std.mem.eql(u8, args[2], "--where"))
        return .{ .mode = .literal, .input = args[2], .value = args[3], .terms = args[3..] };
    var position: usize = 2;
    var parsed: Arguments = .{ .mode = .literal, .input = "" };
    if (args.len > position and std.mem.eql(u8, args[position], "--mode")) {
        if (args.len <= position + 1) return error.InvalidArguments;
        parsed.mode = if (std.mem.eql(u8, args[position + 1], "literal")) .literal else if (std.mem.eql(u8, args[position + 1], "normalized")) .normalized else return error.InvalidArguments;
        position += 2;
    }
    while (position < args.len) {
        const option = args[position];
        if (std.mem.eql(u8, option, "--all")) {
            if (parsed.all) return error.InvalidArguments;
            parsed.all = true;
            position += 1;
        } else if (std.mem.eql(u8, option, "--frontmatter")) {
            if (parsed.frontmatter) return error.InvalidArguments;
            parsed.frontmatter = true;
            position += 1;
        } else if (std.mem.eql(u8, option, "--where")) {
            if (args.len <= position + 2) return error.InvalidArguments;
            if (parsed.where_count == frontmatter.max_filters) return error.InvalidFilter;
            parsed.where[parsed.where_count] = .{ .pointer = args[position + 1], .json = args[position + 2] };
            parsed.where_count += 1;
            position += 3;
        } else break;
    }
    if ((parsed.all or parsed.frontmatter or parsed.where_count != 0) and !std.mem.eql(u8, args[1], "search") and !std.mem.eql(u8, args[1], "scan")) return error.InvalidArguments;
    for (parsed.where[0..parsed.where_count]) |condition| {
        if (try frontmatter.selectorKind(condition.pointer) == .metadata and !parsed.frontmatter) return error.InvalidArguments;
    }
    if (args.len <= position) return error.InvalidArguments;
    if (parsed.all) {
        if (args.len == position + 1) return error.EmptyQuery;
    } else if (args.len != position + 2) return error.InvalidArguments;
    parsed.input = args[position];
    parsed.value = args[position + 1];
    parsed.terms = args[position + 1 ..];
    return parsed;
}

fn prepareFilters(allocator: std.mem.Allocator, parsed: *const Arguments) ![]frontmatter.Filter {
    const filters = try allocator.alloc(frontmatter.Filter, parsed.where_count);
    var initialized: usize = 0;
    errdefer {
        for (filters[0..initialized]) |*filter| filter.deinit(allocator);
        allocator.free(filters);
    }
    for (parsed.where[0..parsed.where_count], filters) |raw, *filter| {
        filter.* = try frontmatter.Filter.init(allocator, raw.pointer, raw.json);
        initialized += 1;
    }
    return filters;
}

fn findMatches(allocator: std.mem.Allocator, index: *const search.Index, parsed: *const Arguments, filters: []const frontmatter.Filter, scan: bool, diagnostic: ?*?frontmatter.Diagnostic) ![]search.Match {
    if (diagnostic) |out| out.* = null;
    var projection: ?frontmatter.Projection = if (parsed.frontmatter or filters.len != 0) try frontmatter.Projection.init(allocator, index.documents, filters, parsed.frontmatter, diagnostic) else null;
    defer if (projection) |*value| value.deinit(allocator);
    // 投影は原文とpostingsを借用するだけで、Index.deinitは呼ばない。
    const projected: search.Index = if (projection) |value| .{ .allocator = allocator, .documents = value.documents, .postings = index.postings, .mode = index.mode, .eligible = value.eligible } else index.*;
    const matches = if (scan)
        (if (parsed.all) try projected.scanAll(allocator, parsed.terms, parsed.mode) else try projected.scanMode(allocator, parsed.value, parsed.mode))
    else
        (if (parsed.all) try projected.searchAll(allocator, parsed.terms, parsed.mode) else try projected.searchMode(allocator, parsed.value, parsed.mode));
    if (projection) |value| value.restore(matches);
    return matches;
}

fn isMarkdown(path: []const u8) bool {
    const extension = std.fs.path.extension(path);
    return std.ascii.eqlIgnoreCase(extension, ".md") or std.ascii.eqlIgnoreCase(extension, ".markdown");
}

// 文書配列・パス・本文と途中の確保は、呼び出し元のarenaで一括管理する。
// 原文は読み取り専用で開き、走査順に依存しない文書IDとなるようパス順に並べる。
fn loadCorpus(allocator: std.mem.Allocator, io: std.Io, root_path: []const u8) ![]const search.Document {
    const previous = profile.enter(.read);
    defer profile.leave(previous);
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
    const previous = profile.enter(.output);
    defer profile.leave(previous);
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
    _ = @import("normalize.zig");
}

test "ANDのCLIは入力後のオプション風文字列を語として扱う" {
    const old = try parseArguments(&.{ "zigurui", "search", "notes.idx", "--all" });
    try std.testing.expect(!old.all);
    try std.testing.expectEqualStrings("--all", old.value);
    const all = try parseArguments(&.{ "zigurui", "search", "--mode", "normalized", "--all", "notes.idx", "a b", "--all" });
    try std.testing.expect(all.all and all.mode == .normalized);
    try std.testing.expectEqual(@as(usize, 2), all.terms.len);
    try std.testing.expectEqualStrings("a b", all.terms[0]);
    try std.testing.expectEqualStrings("--all", all.terms[1]);
    const path = try parseArguments(&.{ "zigurui", "search", "./--all", "word" });
    try std.testing.expectEqualStrings("./--all", path.input);
    try std.testing.expectError(error.EmptyQuery, parseArguments(&.{ "zigurui", "search", "--all", "notes.idx" }));
    try std.testing.expectError(error.InvalidArguments, parseArguments(&.{ "zigurui", "search", "--all" }));
    try std.testing.expectError(error.InvalidArguments, parseArguments(&.{ "zigurui", "index", "--all", "docs", "notes.idx" }));
    try std.testing.expectError(error.InvalidArguments, parseArguments(&.{ "zigurui", "search", "notes.idx", "a", "b" }));
}

test "frontmatterのCLIはinput前のwhereを2引数ずつ受け本文語を再解釈しない" {
    const parsed = try parseArguments(&.{ "zigurui", "search", "--mode", "normalized", "--where", "/a=b", "1.0", "--frontmatter", "--all", "index", "--where", "word" });
    try std.testing.expect(parsed.frontmatter and parsed.all and parsed.mode == .normalized);
    try std.testing.expectEqual(@as(usize, 1), parsed.where_count);
    try std.testing.expectEqualStrings("/a=b", parsed.where[0].pointer);
    try std.testing.expectEqualStrings("1.0", parsed.where[0].json);
    try std.testing.expectEqualStrings("--where", parsed.terms[0]);
    try std.testing.expectError(error.InvalidArguments, parseArguments(&.{ "zigurui", "search", "--where", "/x", "true", "index", "word" }));
    try std.testing.expectError(error.InvalidArguments, parseArguments(&.{ "zigurui", "index", "--frontmatter", "docs", "index" }));
    const schema = try parseArguments(&.{ "zigurui", "schema", "index" });
    try std.testing.expectEqualStrings("index", schema.input);
    try std.testing.expectError(error.InvalidArguments, parseArguments(&.{ "zigurui", "schema", "--frontmatter", "index" }));
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
    const parsed: Arguments = .{ .mode = .normalized, .input = "", .all = true, .terms = &.{ "ガ", "ロ" }, .frontmatter = true };
    for ([_]bool{ false, true }) |scan| {
        const matches = try findMatches(allocator, &index, &parsed, &.{filter}, scan, null);
        defer allocator.free(matches);
        try std.testing.expectEqualSlices(search.Match, &.{.{ .doc_id = 0, .offset = prefix.len + 3, .end = prefix.len + 6 }}, matches);
    }
    var single: Arguments = .{ .mode = .normalized, .input = "", .value = "header-only", .frontmatter = true };
    const body = try findMatches(allocator, &index, &single, &.{}, false, null);
    defer allocator.free(body);
    try std.testing.expectEqual(@as(usize, 0), body.len);
    single.frontmatter = false;
    const raw = try findMatches(allocator, &index, &single, &.{}, false, null);
    defer allocator.free(raw);
    try std.testing.expectEqual(@as(usize, 1), raw.len);
    single.frontmatter = true;
    single.mode = .literal;
    single.value = "ｶﾞ";
    const literal = try findMatches(allocator, &index, &single, &.{}, false, null);
    defer allocator.free(literal);
    try std.testing.expectEqualSlices(search.Match, &.{.{ .doc_id = 0, .offset = prefix.len + 10, .end = prefix.len + 16 }}, literal);
}

test "候補外やfilter不一致の不正frontmatterも全体エラーにする" {
    const allocator = std.testing.allocator;
    const documents = [_]search.Document{
        .{ .path = "a", .text = "---\nstate: ready\n---\nknown" },
        .{ .path = "b", .text = "---\nstate: [broken\n---\nother" },
    };
    var index = try search.Index.init(allocator, &documents);
    defer index.deinit();
    const parsed: Arguments = .{ .mode = .literal, .input = "", .value = "nonexistent", .frontmatter = true };
    var filter = try frontmatter.Filter.init(allocator, "/state", "\"ready\"");
    defer filter.deinit(allocator);
    try std.testing.expectError(error.InvalidFrontmatter, findMatches(allocator, &index, &parsed, &.{filter}, false, null));
    try std.testing.expectError(error.InvalidFrontmatter, findMatches(allocator, &index, &parsed, &.{filter}, true, null));
}

test "where条件は64個まで許容し65個目を拒否する" {
    const allocator = std.testing.allocator;
    var args: std.ArrayList([]const u8) = .empty;
    defer args.deinit(allocator);
    try args.appendSlice(allocator, &.{ "zigurui", "search", "--frontmatter" });
    for (0..64) |_| try args.appendSlice(allocator, &.{ "--where", "/x", "true" });
    try args.appendSlice(allocator, &.{ "index", "word" });
    const parsed = try parseArguments(args.items);
    try std.testing.expectEqual(@as(usize, 64), parsed.where_count);
    try args.insertSlice(allocator, 3, &.{ "--where", "/x", "true" });
    try std.testing.expectError(error.InvalidFilter, parseArguments(args.items));
}

test "本文先頭の結合文字でも原文postingsが本文正規化の候補を落とさない" {
    const allocator = std.testing.allocator;
    const prefix = "---\nx: v\n---\n";
    const documents = [_]search.Document{.{ .path = "a", .text = prefix ++ "\u{315}\u{300}A" }};
    var index = try search.Index.initMode(allocator, &documents, .normalized);
    defer index.deinit();
    const parsed: Arguments = .{ .mode = .normalized, .input = "", .frontmatter = true, .all = true, .terms = &.{ "a", "\u{300}\u{315}" } };
    for ([_]bool{ false, true }) |scan| {
        const matches = try findMatches(allocator, &index, &parsed, &.{}, scan, null);
        defer allocator.free(matches);
        try std.testing.expectEqualSlices(search.Match, &.{.{ .doc_id = 0, .offset = prefix.len, .end = prefix.len + 4 }}, matches);
    }
}

test "file条件だけならfrontmatter指定不要でmetadata条件との混在時は必要" {
    const raw = try parseArguments(&.{ "zigurui", "search", "--where", "file:/name", "\"a.md\"", "index", "word" });
    try std.testing.expect(!raw.frontmatter and raw.where_count == 1);
    try std.testing.expectError(error.InvalidArguments, parseArguments(&.{ "zigurui", "search", "--where", "file:/name", "\"a.md\"", "--where", "/state", "\"ready\"", "index", "word" }));
    const mixed = try parseArguments(&.{ "zigurui", "search", "--where", "file:/name", "\"a.md\"", "--where", "/state", "\"ready\"", "--frontmatter", "index", "word" });
    try std.testing.expect(mixed.frontmatter and mixed.where_count == 2);
    try std.testing.expectError(error.InvalidFilter, parseArguments(&.{ "zigurui", "search", "--where", "file:/size", "1", "index", "word" }));
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
    const parsed: Arguments = .{ .mode = .normalized, .input = "", .value = "HEADER-WORD" };
    for ([_]bool{ false, true }) |scan| {
        const matches = try findMatches(allocator, &index, &parsed, &.{filter}, scan, null);
        defer allocator.free(matches);
        try std.testing.expectEqualSlices(search.Match, &.{ .{ .doc_id = 0, .offset = 11, .end = 22 }, .{ .doc_id = 1, .offset = 11, .end = 22 } }, matches);
    }
    var unnormalized = try frontmatter.Filter.init(allocator, "file:/name", "\"NOTE.md\"");
    defer unnormalized.deinit(allocator);
    const no_match = try findMatches(allocator, &index, &parsed, &.{unnormalized}, false, null);
    defer allocator.free(no_match);
    try std.testing.expectEqual(@as(usize, 0), no_match.len);
}

test "builtin pathはv2保存snapshotを使い相対pathも推測で書き換えない" {
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
    const parsed: Arguments = .{ .mode = .literal, .input = "", .value = "body" };
    const found = try findMatches(allocator, &stored.index, &parsed, &.{old}, false, null);
    defer allocator.free(found);
    try std.testing.expectEqual(@as(usize, 1), found.len);
    const absent = try findMatches(allocator, &stored.index, &parsed, &.{changed}, false, null);
    defer allocator.free(absent);
    try std.testing.expectEqual(@as(usize, 0), absent.len);
}

// 各バイトを最大6バイトへescapeし、パス中の制御文字を端末へ直接送らない。
fn escapedPathByte(byte: u8, buffer: *[6]u8) []const u8 {
    const literal: ?[]const u8 = switch (byte) {
        '"' => "\\\"",
        '\\' => "\\\\",
        '\n' => "\\n",
        '\r' => "\\r",
        '\t' => "\\t",
        else => null,
    };
    if (literal) |value| return value;
    if (byte < 0x20 or byte == 0x7f) {
        const hex = "0123456789abcdef";
        buffer.* = .{ '\\', 'u', '0', '0', hex[byte >> 4], hex[byte & 15] };
        return buffer;
    }
    buffer[0] = byte;
    return buffer[0..1];
}

fn writeDiagnostic(io: std.Io, path: []const u8, diagnostic: frontmatter.Diagnostic) !void {
    const stderr = std.Io.File.stderr();
    var buffer: [256]u8 = undefined;
    const prefix = try std.fmt.bufPrint(&buffer, "zigurui: {s}: {s}: file=\"", .{ @tagName(diagnostic.context), @errorName(diagnostic.reason) });
    try stderr.writeStreamingAll(io, prefix);
    var escaped: [6]u8 = undefined;
    for (path) |byte| try stderr.writeStreamingAll(io, escapedPathByte(byte, &escaped));
    try stderr.writeStreamingAll(io, "\"\n");
}

// documentsが解放される前に出力する。書き込み失敗時はmainの汎用診断へ戻す。
fn reportDiagnostic(io: std.Io, documents: []const search.Document, diagnostic: ?frontmatter.Diagnostic, reported: *bool) void {
    const value = diagnostic orelse return;
    writeDiagnostic(io, documents[value.document_index].path, value) catch return;
    reported.* = true;
}

test "diagnostic path escapes terminal control bytes" {
    var buffer: [6]u8 = undefined;
    try std.testing.expectEqualStrings("\\n", escapedPathByte('\n', &buffer));
    try std.testing.expectEqualStrings("\\r", escapedPathByte('\r', &buffer));
    try std.testing.expectEqualStrings("\\t", escapedPathByte('\t', &buffer));
    try std.testing.expectEqualStrings("\\u001b", escapedPathByte(0x1b, &buffer));
    try std.testing.expectEqualStrings("\\u007f", escapedPathByte(0x7f, &buffer));
    try std.testing.expectEqualStrings("\\\"", escapedPathByte('"', &buffer));
    try std.testing.expectEqualStrings("\\\\", escapedPathByte('\\', &buffer));
}
