//! CLIの引数処理、Markdownの収集、索引の保存と検索結果の表示を担当する。
//! 収集した原文と表示用メモリはプロセスのarenaで管理する。読み込んだ索引はgpaで所有し、検索後に解放する。

const std = @import("std");
const profile = @import("profile.zig");
const search = @import("search.zig");
const query = @import("query.zig");
const storage = @import("storage.zig");
const incremental = @import("incremental.zig");
const relevance = @import("relevance.zig");
const ranking = @import("ranking.zig");
const frontmatter = @import("frontmatter.zig");
const Paths = @import("paths.zig").Paths;
const configuration = @import("config.zig");
const update = @import("update.zig");
const version = @import("build_options").version;

const max_index_bytes = 1024 * 1024 * 1024;

const help =
    \\zigurui - ローカルMarkdown検索
    \\
    \\  zigurui index [--mode literal|normalized] [--incremental] <directory> [<index-file>]
    \\  zigurui search [--mode literal|normalized] [<index-file>] <query>
    \\  zigurui scan [--mode literal|normalized] <directory> <query>
    \\  zigurui search [--mode literal|normalized] --all <index-file> <term>...
    \\  zigurui scan [--mode literal|normalized] --all <directory> <term>...
    \\  zigurui schema <index-file>
    \\  search/scanの入力前: [--frontmatter] [--where <selector> <JSON-scalar>]...
    \\  search/scanの入力前: [--rank path|heading|relevance]（既定path、見出し一致を優先）
    \\  zigurui help
    \\  zigurui paths
    \\  zigurui version / --version
    \\  zigurui update --version <tag> [--channel stable|prerelease]
    \\
    \\literalは原文の連続部分一致。normalizedはNFKC・full case foldingを使う。
    \\空白も検索語の一部。空白を含む場合は引用符で囲む。
    \\--allは指定した全語句を同一文書で検索する。順序は問わない。
    \\--frontmatterは本文だけを検索する。--whereはYAMLキーやfile:/name・file:/pathで絞る。
    \\normalized検索には同じmodeで構築した索引が必要。literalは両索引で使える。
    \\searchは保存時点の文書を検索する。変更後はindexを再実行する。
    \\--incrementalは同じ文書ディレクトリとmodeの索引を差分更新する。通常indexは全再構築する。
    \\索引ファイルにはMarkdown以外の拡張子を使用する。
    \\  --explain: relevance順位の説明JSON（通常表示とは別契約）
    \\設定はXDG_CONFIG_HOME/zigurui/config.jsonから読み、CLI指定を優先する。
    \\設定なしの既定はliteralとXDG_DATA_HOME/zigurui/default.idx（pathsで確認）。
    \\--allとschemaでは索引パスを明示する。
    \\
;

/// index・search・scanを実行する。検索結果が0件でも正常終了し、失敗は標準エラーへ出力して終了コード1を返す。
pub fn main(init: std.process.Init) void {
    var reported = false;
    var config_diagnostic: ?[]const u8 = null;
    run(init, &reported, &config_diagnostic) catch |err| {
        if (reported) std.process.exit(1);
        const hint: []const u8 = switch (err) {
            error.UnsupportedVersion, error.UnsupportedSearchProfile, error.IndexModeMismatch => " (index --modeで索引を再構築してください)",
            error.IncrementalNeedsRebuild => " (差分更新の導入には通常のindexで再構築してください)",
            error.SourceRootMismatch => " (索引作成時と同じ文書ディレクトリを指定してください)",
            error.NormalizationTooLarge => " (非ASCIIの正規化は各中間表現4,194,304コードポイントまでです)",
            error.HomeDirectoryUnavailable => " (絶対パスのHOMEまたは各XDG_*_HOMEを設定してください)",
            error.UnsupportedUpdateLocation => " (この配置先はupdateの対象外です。導入元の管理方法で更新してください)",
            error.UpdateFailed => " (更新できませんでした。上記の理由を確認し、必要なら外部インストーラを使ってください)",
            else => "",
        };
        const message = std.fmt.allocPrint(init.arena.allocator(), "zigurui: {s}{s}\n", .{ config_diagnostic orelse @errorName(err), hint }) catch "zigurui: OutOfMemory\n";
        std.Io.File.stderr().writeStreamingAll(init.io, message) catch {};
        if (err == error.InvalidArguments) std.Io.File.stderr().writeStreamingAll(init.io, help) catch {};
        std.process.exit(1);
    };
}

fn run(init: std.process.Init, reported: *bool, config_diagnostic: *?[]const u8) !void {
    var diagnostic: ?frontmatter.Diagnostic = null;
    const allocator = init.arena.allocator();
    const args = try init.minimal.args.toSlice(allocator);
    if (args.len == 1 or (args.len == 2 and (std.mem.eql(u8, args[1], "help") or std.mem.eql(u8, args[1], "--help")))) {
        try std.Io.File.stdout().writeStreamingAll(init.io, help);
        return;
    }
    if (args.len == 2 and (std.mem.eql(u8, args[1], "version") or std.mem.eql(u8, args[1], "--version"))) {
        try std.Io.File.stdout().writeStreamingAll(init.io, "zigurui " ++ version ++ "\n");
        return;
    }
    const showing_paths = args.len == 2 and std.mem.eql(u8, args[1], "paths");
    if (args.len >= 2 and std.mem.eql(u8, args[1], "update")) {
        try update.run(init, args[2..]);
        return;
    }
    // 引数エラーは設定の有無にかかわらず診断する。
    const parsed: ?Arguments = if (showing_paths) null else try parseArguments(args);
    // HOMEがなく設定先を特定できない場合も、既存の明示パス操作は利用できる。
    const config_file = Paths.configFile(allocator, init.environ_map) catch |err| switch (err) {
        error.HomeDirectoryUnavailable => null,
        else => return err,
    };
    const config = try configuration.load(allocator, init.io, config_file, config_diagnostic);
    if (showing_paths) {
        const paths = try Paths.resolve(allocator, init.environ_map);
        const message = try std.fmt.allocPrint(allocator, "config: {s}\nconfig_file: {s}\ndata: {s}\ncache: {s}\nstate: {s}\nindex: {s}\nmode: {s}\n", .{ paths.config, config_file.?, paths.data, paths.cache, paths.state, config.index_path orelse paths.index, @tagName(config.mode) });
        try std.Io.File.stdout().writeStreamingAll(init.io, message);
        return;
    }
    var arguments = parsed.?;
    if (!arguments.mode_specified and !std.mem.eql(u8, args[1], "schema")) arguments.mode = config.mode;
    const searching = std.mem.eql(u8, args[1], "search") or std.mem.eql(u8, args[1], "scan");
    if (searching and !arguments.rank_specified) arguments.rank = config.rank;
    arguments.relevance_settings = config.relevance;
    if (arguments.explain and arguments.rank != .relevance) return error.InvalidArguments;
    const default_index = if (arguments.default_index) config.index_path orelse try Paths.defaultIndex(allocator, init.environ_map) else null;
    if (default_index) |path| {
        if (std.mem.eql(u8, args[1], "index")) arguments.value = path else arguments.input = path;
    }
    profile.init(init.io);
    var succeeded = false;
    const command = if (std.mem.eql(u8, args[1], "search")) "search" else if (std.mem.eql(u8, args[1], "scan")) "scan" else if (std.mem.eql(u8, args[1], "index")) "index" else if (std.mem.eql(u8, args[1], "schema")) "schema" else "unknown";
    defer profile.report(init.io, command, @tagName(arguments.mode), succeeded);
    const filters = try prepareFilters(init.gpa, &arguments);
    defer {
        for (filters) |*filter| filter.deinit(init.gpa);
        init.gpa.free(filters);
    }
    const mode = arguments.mode;
    const input = arguments.input;
    const value = arguments.value;
    if (std.mem.eql(u8, args[1], "index")) {
        // 文書への誤上書きと、索引自身の再取り込みを防ぐ。
        if (isMarkdown(value)) return error.IndexPathMustNotBeMarkdown;
        if (config_file) |path| {
            if (try isConfigFile(allocator, init.io, path, value)) return error.ConfigFileIsIndex;
        }
        const root = try std.Io.Dir.cwd().realPathFileAlloc(init.io, input, allocator);
        var changes: ?incremental.Changes = null;
        // rootとmodeを確認してから現在文書を読む。旧形式を暗黙に変換しない。
        var index = if (arguments.incremental) blk: {
            const old_bytes = try std.Io.Dir.cwd().readFileAlloc(init.io, value, init.gpa, .limited(max_index_bytes));
            var old = try storage.decodeOwned(init.gpa, old_bytes);
            defer old.deinit();
            const old_root = old.source_root orelse return error.IncrementalNeedsRebuild;
            if (!std.mem.eql(u8, old_root, root)) return error.SourceRootMismatch;
            if (old.index.mode != mode) return error.IndexModeMismatch;
            const documents = try loadCorpus(allocator, init.io, root);
            const result = try incremental.reconcile(init.gpa, &old.index, documents);
            changes = result.changes;
            if (result.index) |changed_index| break :blk changed_index;
            // 完全一致でも旧索引の検証と全文書の読取は完了している。
            // 出力に失敗しても既存の索引ファイルには一切書き込まない。
            const message = try indexMessage(allocator, old.index.documents.len, result.changes);
            try std.Io.File.stdout().writeStreamingAll(init.io, message);
            succeeded = true;
            return;
        } else try search.Index.initMode(init.gpa, try loadCorpus(allocator, init.io, root), mode);
        defer index.deinit();
        const bytes = try storage.encodeWithRoot(allocator, &index, root);
        if (bytes.len >= max_index_bytes) return error.IndexTooLarge;
        // 保存成功後にメモリ確保で失敗しないよう、完了表示も先に用意する。
        const message = try indexMessage(allocator, index.documents.len, changes);
        if (default_index != null) try std.Io.Dir.cwd().createDirPath(init.io, std.fs.path.dirname(value).?);
        // 書き込みと同期が成功してから置き換え、途中の失敗で既存索引を壊さない。
        var atomic = try std.Io.Dir.cwd().createFileAtomic(init.io, value, .{ .replace = true });
        defer atomic.deinit(init.io);
        try commitIndex(&atomic, init.io, bytes);
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
        var result = query.execute(init.gpa, &stored.index, arguments.searchRequest(), filters, .index, &diagnostic) catch |err| {
            reportDiagnostic(init.io, stored.index.documents, diagnostic, reported);
            return err;
        };
        defer result.deinit();
        if (arguments.explain) try printExplanation(allocator, init.io, result.scores.?, arguments) else try printMatches(allocator, init.io, stored.index.documents, result.matches);
    } else if (std.mem.eql(u8, args[1], "scan")) {
        const documents = try loadCorpus(allocator, init.io, input);
        // 正解の基準となる全文走査では索引を構築しない。
        const index: search.Index = .{ .allocator = allocator, .documents = documents, .postings = &.{} };
        var result = query.execute(init.gpa, &index, arguments.searchRequest(), filters, .scan, &diagnostic) catch |err| {
            reportDiagnostic(init.io, documents, diagnostic, reported);
            return err;
        };
        defer result.deinit();
        if (arguments.explain) try printExplanation(allocator, init.io, result.scores.?, arguments) else try printMatches(allocator, init.io, documents, result.matches);
    } else return error.InvalidArguments;
    succeeded = true;
}

const Where = struct { pointer: []const u8, json: []const u8 };
const Arguments = struct {
    mode: search.Mode,
    mode_specified: bool = false,
    default_index: bool = false,
    incremental: bool = false,
    input: []const u8,
    value: []const u8 = "",
    all: bool = false,
    rank: ranking.Order = .path,
    rank_specified: bool = false,
    explain: bool = false,
    relevance_settings: relevance.Settings = .{},
    terms: []const []const u8 = &.{},
    frontmatter: bool = false,
    where: [frontmatter.max_filters]Where = undefined,
    where_count: usize = 0,

    fn searchRequest(self: *const Arguments) query.Request {
        return .{
            .terms = if (self.all) .{ .all = self.terms } else .{ .single = self.value },
            .mode = self.mode,
            .frontmatter = self.frontmatter,
            .rank = self.rank,
            .relevance_settings = self.relevance_settings,
        };
    }
};

fn parseArguments(args: []const []const u8) !Arguments {
    if (args.len < 2) return error.InvalidArguments;
    if (!std.mem.eql(u8, args[1], "index") and !std.mem.eql(u8, args[1], "search") and !std.mem.eql(u8, args[1], "scan") and !std.mem.eql(u8, args[1], "schema")) return error.InvalidArguments;
    if (std.mem.eql(u8, args[1], "schema")) {
        if (args.len != 3) return error.InvalidArguments;
        return .{ .mode = .literal, .input = args[2] };
    }
    // 従来の入力名--modeは維持し、新しい明示オプションだけを予約する。
    if (args.len == 4 and !std.mem.eql(u8, args[2], "--all") and !std.mem.eql(u8, args[2], "--frontmatter") and !std.mem.eql(u8, args[2], "--where") and !std.mem.eql(u8, args[2], "--incremental") and !std.mem.eql(u8, args[2], "--rank") and !std.mem.eql(u8, args[2], "--explain"))
        return .{ .mode = .literal, .input = args[2], .value = args[3], .terms = args[3..] };
    var position: usize = 2;
    var parsed: Arguments = .{ .mode = .literal, .input = "" };
    if (args.len > position and std.mem.eql(u8, args[position], "--mode")) {
        if (args.len <= position + 1) return error.InvalidArguments;
        parsed.mode = if (std.mem.eql(u8, args[position + 1], "literal")) .literal else if (std.mem.eql(u8, args[position + 1], "normalized")) .normalized else return error.InvalidArguments;
        parsed.mode_specified = true;
        position += 2;
    }
    while (position < args.len) {
        const option = args[position];
        if (std.mem.eql(u8, option, "--incremental")) {
            if (parsed.incremental or !std.mem.eql(u8, args[1], "index")) return error.InvalidArguments;
            parsed.incremental = true;
            position += 1;
        } else if (std.mem.eql(u8, option, "--all")) {
            if (parsed.all) return error.InvalidArguments;
            parsed.all = true;
            position += 1;
        } else if (std.mem.eql(u8, option, "--frontmatter")) {
            if (parsed.frontmatter) return error.InvalidArguments;
            parsed.frontmatter = true;
            position += 1;
        } else if (std.mem.eql(u8, option, "--explain")) {
            if (parsed.explain) return error.InvalidArguments;
            parsed.explain = true;
            position += 1;
        } else if (std.mem.eql(u8, option, "--rank")) {
            if (parsed.rank_specified or args.len <= position + 1) return error.InvalidArguments;
            parsed.rank = std.meta.stringToEnum(ranking.Order, args[position + 1]) orelse return error.InvalidArguments;
            parsed.rank_specified = true;
            position += 2;
        } else if (std.mem.eql(u8, option, "--where")) {
            if (args.len <= position + 2) return error.InvalidArguments;
            if (parsed.where_count == frontmatter.max_filters) return error.InvalidFilter;
            parsed.where[parsed.where_count] = .{ .pointer = args[position + 1], .json = args[position + 2] };
            parsed.where_count += 1;
            position += 3;
        } else break;
    }
    if ((parsed.all or parsed.frontmatter or parsed.where_count != 0 or parsed.rank_specified or parsed.explain) and !std.mem.eql(u8, args[1], "search") and !std.mem.eql(u8, args[1], "scan")) return error.InvalidArguments;
    for (parsed.where[0..parsed.where_count]) |condition| {
        if (try frontmatter.selectorKind(condition.pointer) == .metadata and !parsed.frontmatter) return error.InvalidArguments;
    }
    if (args.len <= position) return error.InvalidArguments;
    if (parsed.all) {
        if (args.len == position + 1) return error.EmptyQuery;
    } else if (args.len == position + 1 and (std.mem.eql(u8, args[1], "index") or std.mem.eql(u8, args[1], "search"))) {
        parsed.default_index = true;
        parsed.input = args[position];
        parsed.value = args[position];
        parsed.terms = args[position..];
        return parsed;
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

// 設定ファイル自身や、そのシンボリックリンク先を索引で上書きしない。
fn isConfigFile(allocator: std.mem.Allocator, io: std.Io, config_file: []const u8, output: []const u8) !bool {
    const current = try std.Io.Dir.cwd().realPathFileAlloc(io, ".", allocator);
    const absolute_output = try std.fs.path.resolveAlloc(allocator, &.{ current, output });
    if (std.mem.eql(u8, absolute_output, config_file)) return true;
    const actual_config = std.Io.Dir.cwd().realPathFileAlloc(io, config_file, allocator) catch |err| switch (err) {
        error.FileNotFound => return false,
        else => return err,
    };
    const actual_output = std.Io.Dir.cwd().realPathFileAlloc(io, output, allocator) catch |err| switch (err) {
        error.FileNotFound => return false,
        else => return err,
    };
    return std.mem.eql(u8, actual_config, actual_output);
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

fn printExplanation(a: std.mem.Allocator, io: std.Io, results: []const relevance.Result, args: Arguments) !void {
    const phase = profile.enter(.output);
    defer profile.leave(phase);
    // 語句自体とfield値を出さず、採点時の設定と結果をそのまま直列化する。
    const json = try std.json.Stringify.valueAlloc(a, .{
        .schema_version = 1,
        .algorithm = relevance.algorithm,
        .mode = @tagName(args.mode),
        .settings = args.relevance_settings,
        .tie_break = "total_desc,path_utf8_asc",
        .distance_unit = "search_representation_unicode_codepoints",
        .results = results,
    }, .{});
    try std.Io.File.stdout().writeStreamingAll(io, json);
    try std.Io.File.stdout().writeStreamingAll(io, "\n");
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
    _ = incremental;
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

test "file条件だけならfrontmatter指定不要でmetadata条件との混在時は必要" {
    const raw = try parseArguments(&.{ "zigurui", "search", "--where", "file:/name", "\"a.md\"", "index", "word" });
    try std.testing.expect(!raw.frontmatter and raw.where_count == 1);
    try std.testing.expectError(error.InvalidArguments, parseArguments(&.{ "zigurui", "search", "--where", "file:/name", "\"a.md\"", "--where", "/state", "\"ready\"", "index", "word" }));
    const mixed = try parseArguments(&.{ "zigurui", "search", "--where", "file:/name", "\"a.md\"", "--where", "/state", "\"ready\"", "--frontmatter", "index", "word" });
    try std.testing.expect(mixed.frontmatter and mixed.where_count == 2);
    try std.testing.expectError(error.InvalidFilter, parseArguments(&.{ "zigurui", "search", "--where", "file:/size", "1", "index", "word" }));
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

test "差分更新はindexでのみ明示し既定索引も使える" {
    const explicit = try parseArguments(&.{ "zigurui", "index", "--mode", "normalized", "--incremental", "notes", "notes.idx" });
    try std.testing.expect(explicit.incremental);
    try std.testing.expectEqual(search.Mode.normalized, explicit.mode);
    const implicit = try parseArguments(&.{ "zigurui", "index", "--incremental", "notes" });
    try std.testing.expect(implicit.incremental and implicit.default_index);
    try std.testing.expectError(error.InvalidArguments, parseArguments(&.{ "zigurui", "search", "--incremental", "notes.idx", "語" }));
    try std.testing.expectError(error.InvalidArguments, parseArguments(&.{ "zigurui", "index", "--incremental", "--incremental", "notes" }));
}

// 保存の順序を一か所に置き、各段階の失敗を同じ経路で検証する。
fn commitIndex(atomic: anytype, io: std.Io, bytes: []const u8) !void {
    try atomic.file.writeStreamingAll(io, bytes);
    try atomic.file.sync(io);
    try atomic.replace(io);
}

test "書込同期置換の各失敗で旧索引の公開状態を保持する" {
    const Fake = struct {
        const Phase = enum { write, sync, replace, success };
        const State = struct { failure: Phase, staging_bytes: usize = 0, synced: bool = false, replaced: bool = false };
        const File = struct {
            state: *State,
            fn writeStreamingAll(file: @This(), _: std.Io, bytes: []const u8) !void {
                file.state.staging_bytes = @min(3, bytes.len);
                if (file.state.failure == .write) return error.InjectedFailure;
                file.state.staging_bytes = bytes.len;
            }
            fn sync(file: @This(), _: std.Io) !void {
                if (file.state.failure == .sync) return error.InjectedFailure;
                file.state.synced = true;
            }
        };
        file: File,
        fn replace(atomic: *@This(), _: std.Io) !void {
            if (!atomic.file.state.synced) return error.NotSynced;
            if (atomic.file.state.failure == .replace) return error.InjectedFailure;
            atomic.file.state.replaced = true;
        }
    };
    for ([_]Fake.Phase{ .write, .sync, .replace }) |phase| {
        var state: Fake.State = .{ .failure = phase };
        var atomic: Fake = .{ .file = .{ .state = &state } };
        try std.testing.expectError(error.InjectedFailure, commitIndex(&atomic, std.Io.failing, "new index"));
        try std.testing.expect(!state.replaced);
    }
    var state: Fake.State = .{ .failure = .success };
    var atomic: Fake = .{ .file = .{ .state = &state } };
    try commitIndex(&atomic, std.Io.failing, "new index");
    try std.testing.expect(state.synced and state.replaced);
}

fn indexMessage(allocator: std.mem.Allocator, count: usize, changes: ?incremental.Changes) ![]u8 {
    return if (changes) |change|
        std.fmt.allocPrint(allocator, "{d} documents indexed ({d} added, {d} modified, {d} deleted, {d} unchanged)\n", .{ count, change.added, change.modified, change.deleted, change.unchanged })
    else
        std.fmt.allocPrint(allocator, "{d} documents indexed\n", .{count});
}

test "変更なしでも既存の完了表示を維持し表示の確保失敗を返す" {
    const a = std.testing.allocator;
    const message = try indexMessage(a, 3, .{ .unchanged = 3 });
    defer a.free(message);
    try std.testing.expectEqualStrings("3 documents indexed (0 added, 0 modified, 0 deleted, 3 unchanged)\n", message);
    try std.testing.expectError(error.OutOfMemory, indexMessage(std.testing.failing_allocator, 0, .{}));
}
