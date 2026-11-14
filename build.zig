const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});
    const main_module = b.createModule(.{
        .root_source_file = b.path("src/main.zig"),
        .target = target,
        .optimize = optimize,
        .strip = b.option(bool, "strip", "配布用にデバッグ情報とシンボルを除去する"),
    });
    const options = b.addOptions();
    options.addOption(bool, "profile", b.option(bool, "profile", "検索の処理時間と件数を標準エラーへ出力する") orelse false);
    main_module.addOptions("build_options", options);
    main_module.link_libc = true;
    main_module.addIncludePath(b.path("vendor/utf8proc"));
    main_module.addCSourceFile(.{ .file = b.path("vendor/utf8proc/utf8proc.c"), .flags = &.{"-DUTF8PROC_STATIC"} });
    main_module.addCSourceFile(.{ .file = b.path("src/unicode_bridge.c"), .flags = &.{"-DUTF8PROC_STATIC"} });
    main_module.addIncludePath(b.path("vendor/libyaml/include"));
    main_module.addCSourceFiles(.{
        .files = &.{ "vendor/libyaml/src/api.c", "vendor/libyaml/src/parser.c", "vendor/libyaml/src/reader.c", "vendor/libyaml/src/scanner.c", "src/yaml_bridge.c" },
        .flags = &.{ "-DYAML_DECLARE_STATIC", "-DYAML_VERSION_MAJOR=0", "-DYAML_VERSION_MINOR=2", "-DYAML_VERSION_PATCH=5", "-DYAML_VERSION_STRING=\"0.2.5\"" },
    });
    main_module.addAnonymousImport("NormalizationTest", .{ .root_source_file = b.path("testdata/unicode/NormalizationTest.txt") });
    main_module.addAnonymousImport("CaseFoldingTest", .{ .root_source_file = b.path("testdata/unicode/CaseFolding.txt") });

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
