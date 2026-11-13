const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});
    const main_module = b.createModule(.{
        .root_source_file = b.path("src/main.zig"),
        .target = target,
        .optimize = optimize,
    });

    const exe = b.addExecutable(.{
        .name = "zigurui",
        .root_module = main_module,
    });
    b.installArtifact(exe);

    const run = b.addRunArtifact(exe);
    run.addPassthruArgs();
    b.step("run", "実行ファイルを起動する").dependOn(&run.step);

    const tests = b.addTest(.{ .root_module = main_module });
    const run_tests = b.addRunArtifact(tests);
    b.step("test", "テストを実行する").dependOn(&run_tests.step);
}
