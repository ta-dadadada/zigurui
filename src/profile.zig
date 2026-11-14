//! 専用ビルドだけで有効になる、単一スレッドCLI用の排他的な区間計測。
const std = @import("std");
pub const enabled = @import("build_options").profile;
pub const Phase = enum { read, checksum, decode, query_prepare, candidates, span_transform, index_of, original_span, output, cleanup, other };
pub const Count = enum { shortest_posting_count, candidate_count, documents_verified, hit_count, normalized_document_count, normalized_input_bytes, span_points_visited };
var clock_io: ?std.Io = null;
var phase: Phase = .other;
var last: i96 = 0;
var beginning: i96 = 0;
var times: [@typeInfo(Phase).@"enum".field_names.len]u64 = @splat(0);
var counts: [@typeInfo(Count).@"enum".field_names.len]u64 = @splat(0);

/// ioを実行中だけ借用する。通常ビルドでは時計も状態も操作しない。
pub fn init(io: std.Io) void {
    if (!enabled) return;
    clock_io = io;
    phase = .other;
    times = @splat(0);
    counts = @splat(0);
    last = std.Io.Clock.awake.now(io).nanoseconds;
    beginning = last;
}

/// 直前の区間を返す。呼び出し側はleaveで戻す。
pub fn enter(next: Phase) Phase {
    if (!enabled) return .other;
    const io = clock_io orelse return .other;
    const now = std.Io.Clock.awake.now(io).nanoseconds;
    times[@backingInt(phase)] += @intCast(@max(0, now - last));
    last = now;
    const previous = phase;
    phase = next;
    return previous;
}
pub fn leave(previous: Phase) void {
    if (!enabled) return;
    _ = enter(previous);
}
pub fn add(counter: Count, value: usize) void {
    if (!enabled or clock_io == null) return;
    counts[@backingInt(counter)] += value;
}

/// 内容・検索語・パスを含まない集計を1行出力する。出力自体は総時間に含めない。
pub fn report(io: std.Io, command: []const u8, mode: []const u8, ok: bool) void {
    if (!enabled) return;
    _ = enter(.other);
    var buffer: [4096]u8 = undefined;
    var writer: std.Io.Writer = .fixed(&buffer);
    writer.print("{{\"schema_version\":1,\"profile\":true,\"command\":\"{s}\",\"mode\":\"{s}\",\"status\":\"{s}\",\"total_ns\":{d},\"timings_ns\":{{", .{ command, mode, if (ok) "ok" else "error", last - beginning }) catch return;
    inline for (@typeInfo(Phase).@"enum".field_names, 0..) |field, i| {
        writer.print("{s}\"{s}\":{d}", .{ if (i == 0) "" else ",", field, times[i] }) catch return;
    }
    writer.writeAll("},\"counts\":{") catch return;
    inline for (@typeInfo(Count).@"enum".field_names, 0..) |field, i| {
        writer.print("{s}\"{s}\":{d}", .{ if (i == 0) "" else ",", field, counts[i] }) catch return;
    }
    writer.writeAll("}}\n") catch return;
    std.Io.File.stderr().writeStreamingAll(io, writer.buffered()) catch {};
    clock_io = null;
}
