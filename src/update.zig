//! 実行中のバイナリの更新を、組み込んだ既存インストーラへ委譲する。
const std = @import("std");

const installer = @embedFile("InstallerScript");

pub const help =
    \\zigurui update --version <tag> [--channel stable|prerelease]
    \\
    \\版の指定は必須。プレリリースには --channel prerelease も指定する。
    \\実行中の実体バイナリがインストーラの管理下にある場合だけ更新する。
    \\同版の再導入や明示した旧版への切替も可能。設定・索引・原本は保持する。
    \\miseなどで管理している場合は、その管理方法で更新する。
    \\
;

/// 引数と作業用メモリはinitのarenaを借用する。生成した子プロセスは終了を待って解放する。
/// 更新対象は実行中の実体の場所に固定し、管理記録や配布物の検証はinstallerが行う。
pub fn run(init: std.process.Init, args: []const []const u8) !void {
    if (args.len == 1 and (std.mem.eql(u8, args[0], "--help") or std.mem.eql(u8, args[0], "-h"))) {
        try std.Io.File.stdout().writeStreamingAll(init.io, help);
        return;
    }
    // --bin-dirや--updateを利用者の引数から注入させない。値の検証はinstallerに集約する。
    var position: usize = 0;
    while (position < args.len) : (position += 2) {
        if (position + 1 == args.len or (!std.mem.eql(u8, args[position], "--version") and !std.mem.eql(u8, args[position], "--channel"))) return error.InvalidArguments;
    }
    const allocator = init.arena.allocator();
    const executable = try std.process.executablePathAlloc(init.io, allocator);
    if (!std.mem.eql(u8, std.fs.path.basename(executable), "zigurui")) return error.UnsupportedUpdateLocation;
    const directory = std.fs.path.dirname(executable) orelse return error.UnsupportedUpdateLocation;
    var argv: std.ArrayList([]const u8) = .empty;
    try argv.appendSlice(allocator, &.{ "/bin/sh", "-s", "--", "--update", "--bin-dir", directory });
    try argv.appendSlice(allocator, args);
    var child = try std.process.spawn(init.io, .{ .argv = argv.items, .stdin = .pipe });
    defer child.kill(init.io);
    try child.stdin.?.writeStreamingAll(init.io, installer);
    child.stdin.?.close(init.io);
    child.stdin = null;
    const result = try child.wait(init.io);
    if (!result.success()) return error.UpdateFailed;
}
