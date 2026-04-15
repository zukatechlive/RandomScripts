"""
Build Doctor v3.0
Dark IDE-style C++ / C# build tool using pywebview for the UI.

Requirements:
    pip install pywebview

Run:
    python build_doctor.py
"""

import os
import re
import subprocess
import threading
import json
import webview

APP_TITLE   = "Build Doctor"
APP_VERSION = "9.0"



# Hardcoded fallback path — used if vswhere auto-detection fails.
VCVARS64_OVERRIDE = r"C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"


def get_vswhere_path():
    for env_var in ("ProgramFiles(x86)", "ProgramFiles"):
        program_files = os.environ.get(env_var, "")
        if not program_files:
            continue
        path = os.path.join(
            program_files,
            "Microsoft Visual Studio",
            "Installer",
            "vswhere.exe"
        )
        if os.path.exists(path):
            return path
    return None


def find_vcvars64():
    vswhere = get_vswhere_path()
    if vswhere:
        try:
            result = subprocess.check_output([
                vswhere,
                "-latest",
                "-products", "*",
                "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property", "installationPath"
            ], text=True).strip()

            if result:
                vcvars = os.path.join(result, "VC", "Auxiliary", "Build", "vcvars64.bat")
                if os.path.exists(vcvars):
                    return vcvars
        except Exception:
            pass

    if os.path.exists(VCVARS64_OVERRIDE):
        return VCVARS64_OVERRIDE

    return None


# ─────────────────────────────────────────────
# Project Scanner
# ─────────────────────────────────────────────

def scan_project(folder):
    result = {
        "cpp": [], "c": [], "headers": [],
        "sln": [], "vcxproj": [], "csproj": [], "cs": [],
        "cmake": [], "makefile": []
    }

    for rootdir, _, files in os.walk(folder):
        for file in files:
            full = os.path.join(rootdir, file)
            rel  = os.path.relpath(full, folder).replace("\\", "/")
            lo   = file.lower()

            if lo.endswith(".cpp"):
                result["cpp"].append(rel)
            elif lo.endswith(".c"):
                result["c"].append(rel)
            elif lo.endswith((".h", ".hpp")):
                result["headers"].append(rel)
            elif lo.endswith(".sln"):
                result["sln"].append(rel)
            elif lo.endswith(".vcxproj"):
                result["vcxproj"].append(rel)
            elif lo.endswith(".csproj"):
                result["csproj"].append(rel)
            elif lo.endswith(".cs"):
                result["cs"].append(rel)
            elif lo == "cmakelists.txt":
                result["cmake"].append(rel)
            elif lo == "makefile":
                result["makefile"].append(rel)

    return result


# ─────────────────────────────────────────────
# Build Script Generation
# ─────────────────────────────────────────────

def generate_vcxproj(folder, config, scan):
    """
    Generate a minimal but complete .vcxproj for a folder that has .cpp/.c
    source files but no existing Visual Studio project file.
    Returns the path to the generated .vcxproj.
    """
    import uuid as _uuid

    proj_name  = os.path.basename(folder.rstrip("\\/")) or "project"
    proj_guid  = "{" + str(_uuid.uuid4()).upper() + "}"
    conf       = config.get("config",  "Release")
    # VS 2026 / MSBuild v144 requires "stdcpp17" format, NOT "c++17"
    # "/std:c++17" -> strip "/std:" -> "c++17" -> map to MSBuild canonical form
    _std_raw = config.get("std", "/std:c++17").replace("/std:", "")  # e.g. "c++17"
    _std_map = {"c++14": "stdcpp14", "c++17": "stdcpp17", "c++20": "stdcpp20",
                "c++latest": "stdcpplatest", "c11": "stdc11", "c17": "stdc17"}
    std = _std_map.get(_std_raw.lower(), "stdcpp17")
    rt         = config.get("runtime", "/MT")
    opt        = config.get("opt",     "/O2")
    extra_inc  = config.get("extraInc", "")
    extra_lib  = config.get("extraLib", "")

    # Build extra include/lib strings for the XML
    inc_parts = [p.strip() for p in extra_inc.split(";") if p.strip()]
    lib_parts = [p.strip() for p in extra_lib.split(";") if p.strip()]
    inc_str   = ";".join(inc_parts + ["%(AdditionalIncludeDirectories)"])
    lib_str   = ";".join(lib_parts + ["%(AdditionalDependencies)"]) if lib_parts else "%(AdditionalDependencies)"

    # Collect source files relative to the project folder
    src_files = scan["cpp"] + scan["c"]

    # Map /O2 → Optimize value
    opt_map = {"/O1": "MinSpace", "/O2": "MaxSpeed", "/Od": "Disabled", "/Ox": "Full"}
    opt_val = opt_map.get(opt, "MaxSpeed")

    # Runtime library: /MT → MultiThreaded, /MD → MultiThreadedDLL, etc.
    rt_map = {"/MT": "MultiThreaded", "/MD": "MultiThreadedDLL",
              "/MTd": "MultiThreadedDebug", "/MDd": "MultiThreadedDebugDLL"}
    rt_val = rt_map.get(rt, "MultiThreaded")

    compile_items = "\n".join(
        f'    <ClCompile Include="{f}" />' for f in src_files
    )

    # Determine if any headers exist to add as ClInclude items
    hdr_items = "\n".join(
        f'    <ClInclude Include="{f}" />' for f in scan["headers"]
    ) if scan["headers"] else ""

    vcxproj_content = f"""<?xml version="1.0" encoding="utf-8"?>
<Project DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">

  <ItemGroup Label="ProjectConfigurations">
    <ProjectConfiguration Include="{conf}|x64">
      <Configuration>{conf}</Configuration>
      <Platform>x64</Platform>
    </ProjectConfiguration>
  </ItemGroup>

  <PropertyGroup Label="Globals">
    <ProjectGuid>{proj_guid}</ProjectGuid>
    <RootNamespace>{proj_name}</RootNamespace>
    <WindowsTargetPlatformVersion>10.0</WindowsTargetPlatformVersion>
  </PropertyGroup>

  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.Default.props" />

  <PropertyGroup Condition="'$(Configuration)|$(Platform)'=='{conf}|x64'" Label="Configuration">
    <ConfigurationType>Application</ConfigurationType>
    <UseDebugLibraries>{"true" if "Debug" in conf else "false"}</UseDebugLibraries>
    <PlatformToolset>v143</PlatformToolset>
    <CharacterSet>Unicode</CharacterSet>
  </PropertyGroup>

  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.props" />

  <PropertyGroup Condition="'$(Configuration)|$(Platform)'=='{conf}|x64'">
    <OutDir>$(SolutionDir)$(Configuration)\\</OutDir>
    <IntDir>$(Configuration)\\</IntDir>
    <TargetName>{proj_name}</TargetName>
  </PropertyGroup>

  <ItemDefinitionGroup Condition="'$(Configuration)|$(Platform)'=='{conf}|x64'">
    <ClCompile>
      <WarningLevel>Level3</WarningLevel>
      <Optimization>{opt_val}</Optimization>
      <PreprocessorDefinitions>NDEBUG;_CONSOLE;%(PreprocessorDefinitions)</PreprocessorDefinitions>
      <LanguageStandard>{std}</LanguageStandard>
      <RuntimeLibrary>{rt_val}</RuntimeLibrary>
      <ExceptionHandling>Sync</ExceptionHandling>
      <AdditionalIncludeDirectories>{inc_str}</AdditionalIncludeDirectories>
    </ClCompile>
    <Link>
      <SubSystem>Console</SubSystem>
      <GenerateDebugInformation>{"true" if "Debug" in conf else "false"}</GenerateDebugInformation>
      <AdditionalDependencies>{lib_str}</AdditionalDependencies>
    </Link>
  </ItemDefinitionGroup>

  <ItemGroup>
{compile_items}
  </ItemGroup>
{"  <ItemGroup>" + chr(10) + hdr_items + chr(10) + "  </ItemGroup>" if hdr_items else ""}

  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.targets" />

</Project>
"""

    vcxproj_path = os.path.join(folder, f"{proj_name}.vcxproj")
    with open(vcxproj_path, "w", encoding="utf-8") as f:
        f.write(vcxproj_content)

    return vcxproj_path


def generate_build_bat(folder, config):
    vcvars = find_vcvars64()
    if not vcvars:
        raise Exception("Could not find vcvars64.bat — is Visual Studio installed?")

    scan       = scan_project(folder)
    conf       = config.get("config",   "Release")
    extra_inc  = config.get("extraInc", "")
    parallel   = config.get("parallel", True)

    inc_flags        = " ".join(f'/I"{p.strip()}"'       for p in extra_inc.split(";") if p.strip())
    msbuild_parallel = " /m" if parallel else ""

    # ── Auto-generate .vcxproj if we only have raw source files ─────────────
    if not scan["sln"] and not scan["vcxproj"] and (scan["cpp"] or scan["c"]):
        vcxproj_path = generate_vcxproj(folder, config, scan)
        scan["vcxproj"].append(os.path.relpath(vcxproj_path, folder).replace("\\", "/"))

    lines = [
        "@echo off",
        f'cd /d "{folder}"',
        f'call "{vcvars}"',
        "if ERRORLEVEL 1 (",
        "  echo [!] vcvars64.bat failed — Visual Studio environment not loaded.",
        "  exit /b 1",
        ")",
        "echo.",
        "echo === BUILD DOCTOR START ===",
        "echo.",
    ]

    if scan["sln"]:
        sln = scan["sln"][0]
        lines.append(f'MSBuild "{sln}" /p:Configuration={conf}{msbuild_parallel} /nologo /v:m')
        lines.append("if ERRORLEVEL 1 ( echo [!] MSBuild failed. & exit /b 1 )")

    elif scan["vcxproj"]:
        proj = scan["vcxproj"][0]
        lines.append(f'MSBuild "{proj}" /p:Configuration={conf}{msbuild_parallel} /nologo /v:m')
        lines.append("if ERRORLEVEL 1 ( echo [!] MSBuild failed. & exit /b 1 )")

    elif scan["cmake"]:
        lines += [
            "if not exist build mkdir build",
            "cd build",
            f"cmake .. {inc_flags}",
            "if ERRORLEVEL 1 ( echo [!] CMake configure failed. & exit /b 1 )",
            f"cmake --build . --config {conf}",
            "if ERRORLEVEL 1 ( echo [!] CMake build failed. & exit /b 1 )",
        ]

    else:
        lines.append("echo [!] No buildable files found.")
        lines.append("exit /b 1")

    lines += [
        "echo.",
        "echo === BUILD COMPLETE ===",
        "exit /b 0",
    ]

    bat_path = os.path.join(folder, "build.bat")
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return bat_path, scan


# ─────────────────────────────────────────────
# Diagnosis Patterns
# ─────────────────────────────────────────────

PATTERNS = [
    # ── C++ compiler errors ───────────────────────────────────────────────────
    (r"fatal error C1083",          "Missing include file — check /I paths or install the dependency."),
    # ── Luau-specific version mismatch errors ────────────────────────────────
    (r"error C2051.*BytecodeUtils",  "Luau version mismatch: BytecodeUtils.h enum changed between versions. Run FIX LUAU to re-sync headers and sources from the same commit."),
    (r"C4003.*LUAU_FASTFLAGVARIABLE","Luau version mismatch: LUAU_FASTFLAGVARIABLE macro signature changed (old: 2 args, new: 1 arg). Run FIX LUAU to sync headers/sources to same commit."),
    (r"C2327.*BytecodeBuilder.*constants|C2065.*hasConstants", "Luau API change: BytecodeBuilder members renamed between versions. Run FIX LUAU to re-sync headers and .cpp files from same repo commit."),
    (r"'LuauBytecodeType'.*undeclared", "Luau version mismatch: LuauBytecodeType moved or renamed. Run FIX LUAU to re-sync all Luau headers from the cloned repo."),
    (r"LNK2001|LNK2019",            "Unresolved external symbol — missing .lib or implementation file."),
    (r"LNK1104",                    "Linker cannot open file — add /LIBPATH or install the missing library."),
    (r"LNK1120",                    "Linker: too many unresolved externals — see preceding LNK2001/LNK2019 errors above."),
    (r"LNK1169",                    "Linker: multiply defined symbol — same symbol defined in multiple .obj files. Check for duplicate .cpp entries."),
    (r"LNK1181",                    "Linker cannot open input file — a required .lib or .obj is missing from the output or lib paths."),
    (r"cannot open file.*\.lib",    "Missing .lib file — add /LIBPATH or install the dependency."),
    (r"'cl' is not recognized",     "MSVC environment not loaded — vcvars64.bat failed or VS not found."),
    (r"'MSBuild' is not recognized","MSBuild not on PATH — run from a VS Developer Command Prompt or let Build Doctor load vcvars64.bat."),
    (r"cmake.*is not recognized",   "CMake not found — install CMake and add to PATH."),
    (r"ninja.*is not recognized",   "Ninja not found — install Ninja build system."),
    (r"\bSDL\b",                    "Possible SDL dependency missing — check SDL2 include/lib paths."),
    (r"openssl",                    "OpenSSL dependency missing — click FIX DEPS to install via vcpkg automatically."),
    (r"ZSTD_decompress|error C3861.*zstd|zstd(?:\.h|/zstd\.h)",
                                    "ZSTD library missing — click FIX DEPS to install via vcpkg automatically."),
    (r"warning C4101",              "C4101: unreferenced local variable — click FIX DEPS to suppress automatically."),
    (r"boost",                      "Possible Boost dependency missing — set BOOST_ROOT or add to include paths."),
    (r"error C2065",                "Undeclared identifier — missing include or wrong namespace."),
    (r"error C2664",                "Type mismatch — function argument type error."),
    (r"error C3861",                "Identifier not found — possible missing include or typo."),
    (r"error C2059",                "C++ syntax error — invalid token in expression. Check for stray characters, misplaced operators, or unclosed strings."),
    (r"error C2143",                "C++ syntax error — missing token before identifier. Likely a missing ';' or unbalanced brace."),
    (r"error C2440",                "C++ type-conversion error — incompatible types. Add an explicit cast or check pointer/reference types."),
    (r"error C2039",                "C++ member not found in type — wrong object type or the required #include for the full class definition is missing."),
    (r"error C4996",                "C++ deprecated function warning elevated to error — use the recommended replacement or add #pragma warning(disable: 4996)."),
    (r"error C4244",                "C++ narrowing conversion warning elevated to error — add an explicit cast to silence it."),
    (r"error C2248",                "C++ cannot access private/protected member — use a public accessor method or add a friend declaration."),
    (r"error C2027",                "C++ use of undefined type — forward declaration found but full definition needed here. Add the correct #include."),
    (r"error C3646",                "C++ unknown override specifier — 'override'/'final' require /std:c++11 or later. Update the C++ Standard in Settings."),
    (r"error C2220",                "C++ warning treated as error (/WX flag) — fix the underlying warnings or remove /WX from compiler flags."),
    (r"error C1010",                "C++ unexpected end of file while looking for precompiled header — add #include \"pch.h\" (or stdafx.h) as first line, or disable PCH."),
    (r"error C2007",                "#define syntax error — malformed macro definition."),
    (r"error C2001",                "Newline in constant — an unclosed string literal spans across lines. Check for a missing closing quote."),
    # ── C# / MSBuild errors ───────────────────────────────────────────────────
    (r"error CS2001",               "C# source file not found — a .csproj references a .cs file that is missing or moved. Auto-fix available: run Fix CS Files."),
    (r"error CS0246",               "C# type or namespace not found — missing using directive or assembly reference. Auto-fix available: run Fix CS Files."),
    (r"error CS0234",               "C# namespace member not found — check using directives and assembly references."),
    (r"error CS1061",               "C# member does not exist — possible API change or wrong object type."),
    (r"error CS0103",               "C# name does not exist in context — missing using directive or undeclared variable."),
    (r"error CS1503",               "C# argument type mismatch — wrong type passed to a method."),
    (r"error CS0029",               "C# implicit conversion error — incompatible types assigned."),
    (r"error CS0117",               "C# member not found on type — check spelling or assembly reference."),
    (r"error CS0122",               "C# inaccessible member — check access modifiers (private/internal)."),
    (r"error CS1002",               "C# syntax error: expected semicolon — check for missing ';'."),
    (r"error CS1513",               "C# syntax error: expected closing brace — check brace matching."),
    (r"error CS8019",               "C# unnecessary using directive — can be cleaned up safely."),
    (r"error CS0006",               "C# metadata file not found — a referenced DLL is missing from disk. Check Reference HintPaths in .csproj."),
    (r"error CS0115",               "C# override has no matching base method — base class may have changed. Verify the method signature matches."),
    (r"error CS0120",               "C# object reference required — calling an instance member from a static context. Add 'static' to the method or create an instance."),
    (r"error CS0260",               "C# partial class declaration mismatch — all partial declarations must use the same access modifier."),
    (r"error CS7036",               "C# required argument not supplied — a method call is missing one or more required parameters."),
    (r"error CS8632",               "C# nullable annotation requires nullable context — add '#nullable enable' or set <Nullable>enable</Nullable> in .csproj."),
    (r"error CS0579",               "C# duplicate attribute — same attribute applied more than once where only one is allowed."),
    (r"error CS1579",               "C# foreach not applicable — the collection type does not implement IEnumerable. Check the type or add GetEnumerator()."),
    (r"error CS0433",               "C# ambiguous reference — same type defined in multiple assemblies. Remove the duplicate reference or use extern alias."),
    (r"error CS0266",               "C# explicit cast required — cannot implicitly convert between these types. Add an explicit cast."),
    (r"error CS0305",               "C# wrong number of type arguments — check generic type parameter count."),
    (r"error CS0411",               "C# type arguments cannot be inferred — provide explicit type arguments for the generic method call."),
    (r"error CS1061",               "C# member does not exist on type — possible API change or wrong target type."),
    (r"The referenced component .* could not be found",
                                    "MSBuild reference not found — a NuGet package or DLL is missing. Run NuGet Restore or check .csproj references."),
    (r"NU1903|NU1904",              "NuGet vulnerability warning — consider upgrading the flagged package(s)."),
    (r"NU1101|NU1102|NU1103",       "NuGet package not found — package ID or version may not exist in any configured feed. Check nuget.config sources."),
    (r"packages\.config.*not found|restore.*package|NuGet.*restore",
                                    "NuGet packages not restored — click 'NuGet Restore' in the sidebar or run 'dotnet restore' in a terminal."),
    (r"error MSB3073",              "MSBuild: command-line task exited non-zero — check PreBuild/PostBuild event commands for errors."),
    (r"error MSB4019",              "MSBuild: imported .targets/.props file not found — a NuGet package or SDK is missing. Try NuGet Restore."),
    (r"error MSB3021",              "MSBuild: unable to copy file — output file may be locked by a running process. Close the app/debugger and rebuild."),
    (r"error MSB3026",              "MSBuild: file copy failed after retries — close anything locking the output DLL/EXE, then rebuild."),
    (r"error MSB4006",              "MSBuild: circular dependency detected in the project — check ProjectReference loops."),
    (r"MSBUILD : error",            "MSBuild engine error — check the project file for malformed XML or a missing import target."),
    (r"NETSDK1045",                 ".NET SDK does not support this TargetFramework — install a newer .NET SDK that targets the required version."),
    (r"NETSDK1138",                 "TargetFramework is out of support — update <TargetFramework> to a currently supported version."),
    (r"NETSDK1004",                 "assets.json not found — run 'dotnet restore' to regenerate it."),
    (r"Could not load file or assembly",
                                    "Assembly load failure — DLL missing or wrong architecture (x86 vs x64). Check output directory and project references."),
    (r"The type initializer for .* threw an exception",
                                    "Static initializer exception — a static constructor or static field initializer crashed. Check for null refs or missing config."),
    (r"access.*denied|permission.*denied",
                                    "File access denied — output file may be locked by another process (debugger, running app). Close it and rebuild."),
    (r"disk.*full|no space left",   "Disk is full — free up space on the output drive."),
]


# ─────────────────────────────────────────────
# XML Helpers (shared by all .csproj fixers)
# ─────────────────────────────────────────────

def _parse_csproj(path):
    """Return (ET.ElementTree, root, xmlns_uri) or raise."""
    import xml.etree.ElementTree as ET
    tree = ET.parse(path)
    root = tree.getroot()
    m    = re.match(r"^\{(.*?)\}", root.tag)
    xmlns = m.group(1) if m else ""
    return tree, root, xmlns


def _tag(xmlns, name):
    return f"{{{xmlns}}}{name}" if xmlns else name


def _strip_ns(tag):
    return re.sub(r"^\{.*?\}", "", tag)


def _write_csproj(tree, path, xmlns):
    import xml.etree.ElementTree as ET
    if xmlns:
        ET.register_namespace("", xmlns)
    tree.write(path, encoding="utf-8", xml_declaration=True)


# ─────────────────────────────────────────────
# Auto-Fix Pass 1 & 2: CS2001 + CS0246
# ─────────────────────────────────────────────

def fix_missing_cs_files(folder):
    """
    Pass 1 — CS2001:
      Remove <Compile Include="..."/> entries whose .cs file is gone from disk.

    Pass 2 — CS0246 / missing ProjectReference:
      For each .csproj, detect namespaces/types used in .cs files that aren't
      locally defined, then add <ProjectReference> to matching sibling projects.
    """
    import xml.etree.ElementTree as ET

    actions = []

    all_csproj = []
    for rootdir, _, files in os.walk(folder):
        for fname in files:
            if fname.lower().endswith(".csproj"):
                all_csproj.append(os.path.join(rootdir, fname))

    if not all_csproj:
        return ["No .csproj files found under the selected folder."]

    sibling_map = {}
    for cp in all_csproj:
        stem = os.path.splitext(os.path.basename(cp))[0]
        sibling_map[stem.lower()] = cp

    # ── Pass 1: remove dead <Compile> entries ────────────────────────────────
    for csproj_path in all_csproj:
        proj_dir = os.path.dirname(csproj_path)
        fname    = os.path.basename(csproj_path)
        try:
            tree, root, xmlns = _parse_csproj(csproj_path)
        except ET.ParseError as exc:
            actions.append(f"[SKIP] {fname}: XML parse error — {exc}")
            continue

        removed = []
        for ig in root.iter(_tag(xmlns, "ItemGroup")):
            to_remove = []
            for child in list(ig):
                if _strip_ns(child.tag) != "Compile":
                    continue
                include = child.attrib.get("Include", "")
                if not include.lower().endswith(".cs"):
                    continue
                abs_cs = os.path.normpath(
                    os.path.join(proj_dir, include.replace("\\", os.sep))
                )
                if not os.path.exists(abs_cs):
                    to_remove.append((ig, child, include))
            for ig2, ch, inc in to_remove:
                ig2.remove(ch)
                removed.append(inc)

        if removed:
            _write_csproj(tree, csproj_path, xmlns)
            for inc in removed:
                actions.append(f"[FIXED-CS2001] {fname}: removed dead Compile ref → {inc}")
        else:
            actions.append(f"[OK] {fname}: no dead Compile references.")

    # ── Pass 2: detect & fix missing ProjectReferences (CS0246) ──────────────
    for csproj_path in all_csproj:
        proj_dir = os.path.dirname(csproj_path)
        fname    = os.path.basename(csproj_path)
        stem     = os.path.splitext(fname)[0]

        try:
            tree, root, xmlns = _parse_csproj(csproj_path)
        except ET.ParseError:
            continue

        existing_refs = set()
        for el in root.iter(_tag(xmlns, "ProjectReference")):
            existing_refs.add(
                os.path.normpath(
                    os.path.join(proj_dir,
                                 el.attrib.get("Include", "").replace("\\", os.sep))
                ).lower()
            )

        cs_files = []
        for el in root.iter(_tag(xmlns, "Compile")):
            inc = el.attrib.get("Include", "")
            if inc.lower().endswith(".cs"):
                abs_cs = os.path.normpath(
                    os.path.join(proj_dir, inc.replace("\\", os.sep))
                )
                if os.path.exists(abs_cs):
                    cs_files.append(abs_cs)

        if not cs_files:
            continue

        used_roots = set()
        using_pat  = re.compile(r"^\s*using\s+([\w]+)", re.MULTILINE)
        dotted_pat = re.compile(r"\b([A-Z][A-Za-z0-9_]+)\s*\.")

        for cs in cs_files:
            try:
                src = open(cs, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for m in using_pat.finditer(src):
                used_roots.add(m.group(1).lower())
            for m in dotted_pat.finditer(src):
                used_roots.add(m.group(1).lower())

        used_roots.discard(stem.lower())

        import xml.etree.ElementTree as ET
        added = []
        for candidate_name, candidate_path in sibling_map.items():
            if candidate_name == stem.lower():
                continue
            if candidate_name not in used_roots:
                continue
            norm_candidate = os.path.normpath(candidate_path).lower()
            if norm_candidate in existing_refs:
                continue

            rel            = os.path.relpath(candidate_path, proj_dir)
            candidate_dir  = os.path.dirname(candidate_path)
            candidate_stem = os.path.splitext(os.path.basename(candidate_path))[0]
            dll_hint       = None
            for conf in ("Release", "Debug"):
                for arch in ("x64", "x86", ""):
                    dll_search = os.path.join(
                        candidate_dir, "bin",
                        arch, conf,
                        candidate_stem + ".dll"
                    ) if arch else os.path.join(
                        candidate_dir, "bin", conf, candidate_stem + ".dll"
                    )
                    if os.path.exists(dll_search):
                        dll_hint = dll_search
                        break
                if dll_hint:
                    break

            target_ig = None
            for ig in root.iter(_tag(xmlns, "ItemGroup")):
                for ch in ig:
                    if _strip_ns(ch.tag) == "ProjectReference":
                        target_ig = ig
                        break
                if target_ig is not None:
                    break
            if target_ig is None:
                target_ig = ET.SubElement(root, _tag(xmlns, "ItemGroup"))

            pr = ET.SubElement(target_ig, _tag(xmlns, "ProjectReference"))
            pr.set("Include", rel)
            pr_name      = ET.SubElement(pr, _tag(xmlns, "Name"))
            pr_name.text = candidate_stem

            if dll_hint:
                ref_ig  = ET.SubElement(root, _tag(xmlns, "ItemGroup"))
                ref_el  = ET.SubElement(ref_ig, _tag(xmlns, "Reference"))
                ref_el.set("Include", candidate_stem)
                hint      = ET.SubElement(ref_el, _tag(xmlns, "HintPath"))
                hint.text = os.path.relpath(dll_hint, proj_dir)

            existing_refs.add(norm_candidate)
            added.append(candidate_stem)

        if added:
            _write_csproj(tree, csproj_path, xmlns)
            for a in added:
                actions.append(f"[FIXED-CS0246] {fname}: added ProjectReference → {a}")
        else:
            matches = [n for n in used_roots if n in sibling_map and n != stem.lower()]
            if not matches:
                actions.append(f"[OK] {fname}: no missing project references detected.")

    return actions


# ─────────────────────────────────────────────
# Auto-Fix Pass 3: Duplicate References + Empty ItemGroups
# ─────────────────────────────────────────────

def fix_duplicate_refs(folder):
    """
    Scans all .csproj files and:
      - Removes duplicate <Reference>, <PackageReference>, <Compile>,
        <None>, <Content>, <EmbeddedResource> entries (same Include= value).
      - Removes completely empty <ItemGroup> nodes.
    Returns a list of human-readable action strings.
    """
    import xml.etree.ElementTree as ET
    actions    = []
    DUPE_TAGS  = ("Reference", "PackageReference", "Compile", "None",
                  "Content", "EmbeddedResource", "Analyzer")

    all_csproj = []
    for rootdir, _, files in os.walk(folder):
        for fname in files:
            if fname.lower().endswith(".csproj"):
                all_csproj.append(os.path.join(rootdir, fname))

    if not all_csproj:
        return ["No .csproj files found."]

    for csproj_path in all_csproj:
        fname   = os.path.basename(csproj_path)
        try:
            tree, root, xmlns = _parse_csproj(csproj_path)
        except ET.ParseError as exc:
            actions.append(f"[SKIP] {fname}: XML parse error — {exc}")
            continue

        changed    = False
        dup_count  = 0
        empty_count = 0

        # Pass A: remove duplicate item entries per tag type
        for tag_name in DUPE_TAGS:
            seen = {}
            for ig in root.iter(_tag(xmlns, "ItemGroup")):
                to_remove = []
                for child in list(ig):
                    if _strip_ns(child.tag) != tag_name:
                        continue
                    inc = child.attrib.get("Include", "").strip().lower()
                    if not inc:
                        continue
                    if inc in seen:
                        to_remove.append((ig, child, child.attrib.get("Include", "")))
                    else:
                        seen[inc] = child
                for ig2, ch, orig_inc in to_remove:
                    ig2.remove(ch)
                    dup_count += 1
                    changed    = True
                    actions.append(
                        f"[FIXED-DUP] {fname}: removed duplicate "
                        f"<{tag_name} Include=\"{orig_inc}\" />"
                    )

        # Pass B: remove empty <ItemGroup> nodes
        for ig in list(root):
            if _strip_ns(ig.tag) == "ItemGroup" and len(ig) == 0:
                root.remove(ig)
                empty_count += 1
                changed      = True

        if changed:
            _write_csproj(tree, csproj_path, xmlns)
            if empty_count:
                actions.append(f"[FIXED-EMPTY] {fname}: removed {empty_count} empty <ItemGroup>(s).")

        if dup_count == 0 and empty_count == 0:
            actions.append(f"[OK] {fname}: no duplicate references or empty item groups.")

    return actions


# ─────────────────────────────────────────────
# Auto-Fix Pass 4: Missing AssemblyName / RootNamespace
# ─────────────────────────────────────────────

def fix_assembly_metadata(folder):
    """
    For old-style .csproj files that have a <PropertyGroup> but are missing
    <AssemblyName> or <RootNamespace>, inserts them using the project filename stem.
    SDK-style projects (those that use <Project Sdk=...>) are skipped as they
    default these values automatically.
    Returns a list of action strings.
    """
    import xml.etree.ElementTree as ET
    actions = []

    all_csproj = []
    for rootdir, _, files in os.walk(folder):
        for fname in files:
            if fname.lower().endswith(".csproj"):
                all_csproj.append(os.path.join(rootdir, fname))

    if not all_csproj:
        return ["No .csproj files found."]

    for csproj_path in all_csproj:
        fname = os.path.basename(csproj_path)
        stem  = os.path.splitext(fname)[0]
        try:
            tree, root, xmlns = _parse_csproj(csproj_path)
        except ET.ParseError as exc:
            actions.append(f"[SKIP] {fname}: XML parse error — {exc}")
            continue

        # Skip SDK-style projects — they auto-set assembly metadata
        sdk_attr = root.attrib.get("Sdk", "")
        if sdk_attr:
            actions.append(f"[SKIP] {fname}: SDK-style project, assembly metadata auto-set.")
            continue

        changed = False

        # Find the first non-conditional PropertyGroup
        target_pg = None
        for pg in root.iter(_tag(xmlns, "PropertyGroup")):
            if not pg.attrib.get("Condition", ""):
                target_pg = pg
                break

        if target_pg is None:
            actions.append(f"[SKIP] {fname}: no unconditional PropertyGroup found.")
            continue

        for meta_tag in ("AssemblyName", "RootNamespace"):
            existing = target_pg.find(_tag(xmlns, meta_tag))
            if existing is None:
                new_el      = ET.SubElement(target_pg, _tag(xmlns, meta_tag))
                new_el.text = stem
                changed     = True
                actions.append(f"[FIXED-META] {fname}: added <{meta_tag}>{stem}</{meta_tag}>.")
            elif not (existing.text or "").strip():
                existing.text = stem
                changed       = True
                actions.append(f"[FIXED-META] {fname}: filled blank <{meta_tag}> with '{stem}'.")

        if changed:
            _write_csproj(tree, csproj_path, xmlns)
        else:
            actions.append(f"[OK] {fname}: AssemblyName and RootNamespace already set.")

    return actions


# ─────────────────────────────────────────────
# Auto-Fix Pass 5: C++ Header Include Guards
# ─────────────────────────────────────────────

def fix_cpp_headers(folder):
    """
    Scans all .h / .hpp files. If a file has no include guard
    (#pragma once or #ifndef ... #define ...), prepends '#pragma once' to it.
    Returns a list of action strings.
    """
    actions     = []
    guard_pat   = re.compile(
        r"#\s*pragma\s+once|#\s*ifndef\s+\w+|#\s*if\s+!defined",
        re.IGNORECASE
    )
    headers_found = 0

    for rootdir, _, files in os.walk(folder):
        for fname in files:
            if not fname.lower().endswith((".h", ".hpp")):
                continue
            full          = os.path.join(rootdir, fname)
            rel           = os.path.relpath(full, folder).replace("\\", "/")
            headers_found += 1
            try:
                with open(full, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                if guard_pat.search(content):
                    actions.append(f"[OK] {rel}: include guard already present.")
                    continue
                # Strip UTF-8 BOM if present before prepending
                if content.startswith("\ufeff"):
                    content = content[1:]
                with open(full, "w", encoding="utf-8") as f:
                    f.write("#pragma once\n\n" + content)
                actions.append(f"[FIXED-HDR] {rel}: added #pragma once.")
            except OSError as exc:
                actions.append(f"[ERROR] {rel}: {exc}")

    if headers_found == 0:
        actions.append("No .h / .hpp files found in the project folder.")

    return actions


# ─────────────────────────────────────────────
# Auto-Fix Pass 6 (C++): Missing Include Directories (C1083)
# ─────────────────────────────────────────────

def fix_cpp_missing_includes(folder, last_build_output=""):
    """
    Detects C1083 'Cannot open include file' errors from build output,
    searches the project tree for those headers, and injects the found
    directories into AdditionalIncludeDirectories in every .vcxproj.

    Also performs a blanket scan: any header that lives under the project
    folder but whose parent directory is NOT already in the vcxproj include
    paths will be added.

    Returns a list of action strings.
    """
    import xml.etree.ElementTree as ET

    actions = []

    # ── Step 1: collect missing header names from C1083 lines ────────────────
    missing_names = set()
    c1083_pat = re.compile(
        r"error C1083[^:]*:.*?'([^']+\.h(?:pp)?)'",
        re.IGNORECASE
    )
    for m in c1083_pat.finditer(last_build_output):
        # Strip any leading path components — we only need the filename
        missing_names.add(os.path.basename(m.group(1).replace("/", os.sep).replace("\\", os.sep)))

    if not missing_names and not last_build_output:
        actions.append("[INFO] No build output supplied — running blanket include-path scan instead.")
    elif missing_names:
        actions.append(f"[INFO] C1083 headers to locate: {', '.join(sorted(missing_names))}")

    # ── Step 2: find all .vcxproj files ──────────────────────────────────────
    all_vcxproj = []
    for rootdir, _, files in os.walk(folder):
        for fname in files:
            if fname.lower().endswith(".vcxproj"):
                all_vcxproj.append(os.path.join(rootdir, fname))

    if not all_vcxproj:
        actions.append("[SKIP] No .vcxproj files found under the selected folder.")
        return actions

    # ── Step 3: build a map  header_name -> set of directories ───────────────
    # Walk the whole project tree and record every .h / .hpp location.
    header_dir_map = {}   # filename.lower() -> set of abs dir paths
    for rootdir, dirs, files in os.walk(folder):
        # Skip obvious output/cache dirs
        dirs[:] = [d for d in dirs if d.lower() not in
                   ("x64", "x86", "debug", "release", ".git", ".vs",
                    "relwithdebinfo", "minsizerel", "__pycache__")]
        for fname in files:
            if fname.lower().endswith((".h", ".hpp")):
                key = fname.lower()
                header_dir_map.setdefault(key, set()).add(rootdir)

    # ── Step 4: resolve which dirs need to be added ──────────────────────────
    needed_dirs = set()

    if missing_names:
        for hname in missing_names:
            found = header_dir_map.get(hname.lower(), set())
            if found:
                for d in found:
                    needed_dirs.add(d)
                    actions.append(f"[FOUND] '{hname}' → {d}")
            else:
                actions.append(f"[WARN] '{hname}' not found anywhere under {folder} — add it manually.")
    else:
        # Blanket mode: collect every directory that has headers
        for dirs in header_dir_map.values():
            needed_dirs.update(dirs)

    if not needed_dirs:
        actions.append("[OK] No new include directories to add.")
        return actions

    # ── Step 5: patch each .vcxproj ──────────────────────────────────────────
    for vcxproj_path in all_vcxproj:
        proj_dir = os.path.dirname(vcxproj_path)
        fname    = os.path.basename(vcxproj_path)

        try:
            tree = ET.parse(vcxproj_path)
            root = tree.getroot()
        except ET.ParseError as exc:
            actions.append(f"[SKIP] {fname}: XML parse error — {exc}")
            continue

        ns_m  = re.match(r"^\{(.*?)\}", root.tag)
        xmlns = ns_m.group(1) if ns_m else ""

        def tag(name):
            return f"{{{xmlns}}}{name}" if xmlns else name

        # Gather existing AdditionalIncludeDirectories values so we don't dupe
        existing_dirs = set()
        for aidEl in root.iter(tag("AdditionalIncludeDirectories")):
            for part in re.split(r"[;,]", aidEl.text or ""):
                part = part.strip().rstrip(os.sep + "/")
                if part and part not in ("%(AdditionalIncludeDirectories)",):
                    # Resolve relative to vcxproj dir
                    abs_p = os.path.normpath(os.path.join(proj_dir, part))
                    existing_dirs.add(abs_p.lower())

        new_dirs = [
            d for d in sorted(needed_dirs)
            if os.path.normpath(d).lower() not in existing_dirs
        ]

        if not new_dirs:
            actions.append(f"[OK] {fname}: all required include paths already present.")
            continue

        # Convert to paths relative to the .vcxproj directory
        rel_dirs = []
        for d in new_dirs:
            try:
                rel = os.path.relpath(d, proj_dir)
            except ValueError:
                rel = d   # Different drive — keep absolute
            rel_dirs.append(rel)

        new_inc_str = ";".join(rel_dirs) + ";%(AdditionalIncludeDirectories)"

        # Find all ClCompile PropertyGroup / ItemDefinitionGroup elements
        # that already have AdditionalIncludeDirectories and update them,
        # OR inject into the first ItemDefinitionGroup/ClCompile we find.
        updated = 0
        for idg in root.iter(tag("ItemDefinitionGroup")):
            for clc in idg.iter(tag("ClCompile")):
                aid = clc.find(tag("AdditionalIncludeDirectories"))
                if aid is not None:
                    existing_text = (aid.text or "").rstrip(";")
                    # Remove the trailing sentinel if present
                    existing_text = existing_text.replace(";%(AdditionalIncludeDirectories)", "").rstrip(";")
                    # Append only truly new dirs
                    parts = [p for p in existing_text.split(";") if p.strip()]
                    for rel in rel_dirs:
                        abs_new = os.path.normpath(os.path.join(proj_dir, rel)).lower()
                        if abs_new not in existing_dirs:
                            parts.append(rel)
                    aid.text = ";".join(parts) + ";%(AdditionalIncludeDirectories)"
                    updated += 1
                else:
                    # Create the element
                    new_el      = ET.SubElement(clc, tag("AdditionalIncludeDirectories"))
                    new_el.text = new_inc_str
                    updated += 1

        if updated == 0:
            # No ClCompile found — inject a new ItemDefinitionGroup
            idg_new = ET.SubElement(root, tag("ItemDefinitionGroup"))
            clc_new = ET.SubElement(idg_new, tag("ClCompile"))
            aid_new = ET.SubElement(clc_new, tag("AdditionalIncludeDirectories"))
            aid_new.text = new_inc_str
            updated = 1

        # Write back
        if xmlns:
            ET.register_namespace("", xmlns)
        tree.write(vcxproj_path, encoding="utf-8", xml_declaration=True)

        rel_summary = ", ".join(rel_dirs)
        actions.append(f"[FIXED-INCS] {fname}: injected {len(rel_dirs)} path(s) → {rel_summary}")

    return actions


# ─────────────────────────────────────────────
# Auto-Fix Pass 7: Luau / missing git submodule
# ─────────────────────────────────────────────

# Known Luau header → the subdir inside the Luau repo that contains it
_LUAU_HEADER_DIRS = {
    # ── Flat top-level include (Luau/include/Luau/) ──────────────────────────
    "parseoptions.h":    "include",
    "ast.h":             "Ast/include",
    "bytecodeutils.h":   "Bytecode/include",
    "location.h":        "Ast/include",
    "lexer.h":           "Ast/include",
    "parser.h":          "Ast/include",
    "compiler.h":        "Compiler/include",
    "bytecodebuilder.h": "Compiler/include",
    "codegen.h":         "CodeGen/include",
    "config.h":          "Analysis/include",
    "typecheck.h":       "Analysis/include",
    "frontend.h":        "Analysis/include",
    "common.h":          "Common/include",
    "stringutils.h":     "Common/include",
    "bytecode.h":        "Bytecode/include",
    # ── VM / C-API headers (required for lua_newstate, luau_compile, etc.) ───
    "lua.h":             "VM/include",
    "luaconf.h":         "VM/include",
    "lualib.h":          "VM/include",
    "luacode.h":         "Compiler/include",   # luau_compile lives here
    "luacodegen.h":      "CodeGen/include",
    "lapi.h":            "VM/src",
    "ldo.h":             "VM/src",
    "lgc.h":             "VM/src",
    "lobject.h":         "VM/src",
    "lstate.h":          "VM/src",
    "lstring.h":         "VM/src",
    "ltable.h":          "VM/src",
    "lmem.h":            "VM/src",
    "ldebug.h":          "VM/src",
    "lfunc.h":           "VM/src",
    "lvm.h":             "VM/src",
}

# Luau .cpp source file name → path inside repo where it lives
# (relative to luau_dir root)
_LUAU_SOURCE_MAP = {
    # ── Compiler ─────────────────────────────────────────────────────────────
    "compiler.cpp":        "Compiler/src/Compiler.cpp",
    "confusables.cpp":     "Compiler/src/Confusables.cpp",
    "constantfolding.cpp": "Compiler/src/ConstantFolding.cpp",
    "costmodel.cpp":       "Compiler/src/CostModel.cpp",
    "valuetracking.cpp":   "Compiler/src/ValueTracking.cpp",
    "bytecodebuilder.cpp": "Compiler/src/BytecodeBuilder.cpp",
    # ── Ast ──────────────────────────────────────────────────────────────────
    "lexer.cpp":           "Ast/src/Lexer.cpp",
    "location.cpp":        "Ast/src/Location.cpp",
    "parser.cpp":          "Ast/src/Parser.cpp",
    "stringutils.cpp":     "Ast/src/StringUtils.cpp",
    "tableshape.cpp":      "Ast/src/TableShape.cpp",
    "timetrace.cpp":       "Ast/src/TimeTrace.cpp",
    "types.cpp":           "Ast/src/Types.cpp",
    # ── Bytecode ─────────────────────────────────────────────────────────────
    "bytecodeutils.cpp":   "Bytecode/src/BytecodeUtils.cpp",
    # ── VM (required for lua_newstate / luau_load) ───────────────────────────
    "lapi.cpp":            "VM/src/lapi.cpp",
    "laux.cpp":            "VM/src/laux.cpp",
    "lbaselib.cpp":        "VM/src/lbaselib.cpp",
    "lbitlib.cpp":         "VM/src/lbitlib.cpp",
    "lbuflib.cpp":         "VM/src/lbuflib.cpp",
    "lbuiltins.cpp":       "VM/src/lbuiltins.cpp",
    "lcorolib.cpp":        "VM/src/lcorolib.cpp",
    "ldblib.cpp":          "VM/src/ldblib.cpp",
    "ldebug.cpp":          "VM/src/ldebug.cpp",
    "ldo.cpp":             "VM/src/ldo.cpp",
    "lfunc.cpp":           "VM/src/lfunc.cpp",
    "lgc.cpp":             "VM/src/lgc.cpp",
    "linit.cpp":           "VM/src/linit.cpp",
    "liolib.cpp":          "VM/src/liolib.cpp",
    "lmathlib.cpp":        "VM/src/lmathlib.cpp",
    "lmem.cpp":            "VM/src/lmem.cpp",
    "lobject.cpp":         "VM/src/lobject.cpp",
    "loslib.cpp":          "VM/src/loslib.cpp",
    "lstate.cpp":          "VM/src/lstate.cpp",
    "lstring.cpp":         "VM/src/lstring.cpp",
    "lstrlib.cpp":         "VM/src/lstrlib.cpp",
    "ltable.cpp":          "VM/src/ltable.cpp",
    "ltablib.cpp":         "VM/src/ltablib.cpp",
    "ltm.cpp":             "VM/src/ltm.cpp",
    "ludata.cpp":          "VM/src/ludata.cpp",
    "lutf8lib.cpp":        "VM/src/lutf8lib.cpp",
    "lvmexecute.cpp":      "VM/src/lvmexecute.cpp",
    "lvmload.cpp":         "VM/src/lvmload.cpp",
    "lvmutils.cpp":        "VM/src/lvmutils.cpp",
    # ── Common ───────────────────────────────────────────────────────────────
    "lcode.cpp":           "Compiler/src/lcode.cpp",
}

# Alternative paths tried if the primary mapping is not found on disk
_LUAU_SOURCE_ALT = {
    "compiler.cpp":        ["Compiler/src/compiler.cpp"],
    "lexer.cpp":           ["Ast/src/lexer.cpp"],
    "parser.cpp":          ["Ast/src/parser.cpp"],
    "lcode.cpp":           ["Compiler/src/Lcode.cpp", "VM/src/lcode.cpp"],
    "confusables.cpp":     ["Compiler/src/confusables.cpp"],
    "constantfolding.cpp": ["Compiler/src/Constantfolding.cpp"],
    "costmodel.cpp":       ["Compiler/src/Costmodel.cpp"],
    "location.cpp":        ["Ast/src/location.cpp"],
    "stringutils.cpp":     ["Ast/src/Stringutils.cpp", "Common/src/StringUtils.cpp"],
    "tableshape.cpp":      ["Ast/src/Tableshape.cpp"],
    "timetrace.cpp":       ["Ast/src/Timetrace.cpp", "Common/src/TimeTrace.cpp"],
    "types.cpp":           ["Ast/src/types.cpp"],
    "valuetracking.cpp":   ["Compiler/src/Valuetracking.cpp"],
    "bytecodebuilder.cpp": ["Compiler/src/Bytecodebuilder.cpp"],
    "bytecodeutils.cpp":   ["Bytecode/src/Bytecodeutils.cpp"],
    # VM alternates (case variations across OS/clone)
    "lapi.cpp":            ["VM/src/Lapi.cpp"],
    "laux.cpp":            ["VM/src/Laux.cpp"],
    "lbaselib.cpp":        ["VM/src/Lbaselib.cpp"],
    "lbuiltins.cpp":       ["VM/src/Lbuiltins.cpp"],
    "ldebug.cpp":          ["VM/src/Ldebug.cpp"],
    "ldo.cpp":             ["VM/src/Ldo.cpp"],
    "lfunc.cpp":           ["VM/src/Lfunc.cpp"],
    "lgc.cpp":             ["VM/src/Lgc.cpp"],
    "linit.cpp":           ["VM/src/Linit.cpp"],
    "lmem.cpp":            ["VM/src/Lmem.cpp"],
    "lobject.cpp":         ["VM/src/Lobject.cpp"],
    "lstate.cpp":          ["VM/src/Lstate.cpp"],
    "lstring.cpp":         ["VM/src/Lstring.cpp"],
    "ltable.cpp":          ["VM/src/Ltable.cpp"],
    "ltm.cpp":             ["VM/src/Ltm.cpp"],
    "lvmexecute.cpp":      ["VM/src/Lvmexecute.cpp", "VM/src/lvm.cpp", "VM/src/Lvm.cpp"],
    "lvmload.cpp":         ["VM/src/Lvmload.cpp"],
    "lvmutils.cpp":        ["VM/src/Lvmutils.cpp"],
}

# xxhash lives inside Luau's extern directory
_XXHASH_DIRS = ["extern/xxhash", "extern/xxHash", "Extern/xxhash"]

_LUAU_REPO = "https://github.com/luau-lang/luau.git"

# Headers that are bundled inside the Luau include/Luau/ flat layout
_LUAU_FLAT_HEADERS = {
    "parseoptions.h", "ast.h", "location.h", "lexer.h", "parser.h",
    "compiler.h", "bytecodebuilder.h", "bytecodeutils.h",
}

_LUAU_KNOWN_NAMES = set(_LUAU_HEADER_DIRS.keys()) | set(_LUAU_SOURCE_MAP.keys())


def _is_luau_file(name: str) -> bool:
    return name.lower() in _LUAU_KNOWN_NAMES


def _find_git_root(start: str):
    """Walk up from start looking for a .git directory."""
    cur = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def _run_git(args, cwd, timeout=300):
    """Run a git command; return (returncode, stdout+stderr)."""
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return -1, "'git' not found — install Git and add it to PATH."
    except subprocess.TimeoutExpired:
        return -1, f"git {' '.join(args)} timed out after {timeout}s."
    except Exception as exc:
        return -1, str(exc)


def _find_file_in_repo(luau_dir, filename_lower, primary_rel, alt_rels):
    """
    Try primary path first, then alternates, then a full walk.
    Returns absolute path if found, else None.
    """
    import shutil
    candidates = [primary_rel] + (alt_rels or [])
    for rel in candidates:
        p = os.path.join(luau_dir, rel.replace("/", os.sep))
        if os.path.isfile(p):
            return p
    # Full repo walk fallback
    for rootdir, subdirs, files in os.walk(luau_dir):
        subdirs[:] = [d for d in subdirs if d.lower() not in (".git", "build", "x64")]
        for f in files:
            if f.lower() == filename_lower:
                return os.path.join(rootdir, f)
    return None


def fix_luau_submodule(folder, last_build_output="", emit_line=None):
    """
    Full auto-fix for Luau dependency errors:

      1. Parses C1083 errors for BOTH missing .h headers and missing .cpp sources.
      2. Finds/initializes the Luau git submodule, or clones it fresh.
      3. Copies missing .cpp source files from the Luau repo into the project's
         src/Luau/ directory (creating it if needed).
      4. Patches every .vcxproj to add:
           a) The required Luau include paths (AdditionalIncludeDirectories)
           b) ClCompile entries for any newly copied .cpp source files
      5. Handles xxhash.h by injecting the Luau extern/xxhash include path.
      6. Warns about openssl/err.h (external dep — must be installed separately).

    emit_line(text, cls) — optional callable to stream live log lines to the UI.
    Returns a list of action strings.
    """
    import xml.etree.ElementTree as ET
    import shutil

    actions = []

    def log(text, cls="dim"):
        actions.append(text)
        if emit_line:
            emit_line(text, cls)

    # ── 1. Parse ALL C1083 lines for missing Luau files ───────────────────────
    c1083_pat = re.compile(
        r"error C1083[^:]*:\s*[^:]*:\s*'([^']+)'",
        re.IGNORECASE,
    )

    luau_headers_missing  = set()   # .h/.hpp names (lowercase)
    luau_sources_missing  = set()   # .cpp names (lowercase) referenced in .vcxproj but absent
    xxhash_missing        = False
    openssl_missing       = False
    has_any_luau_error    = False

    for m in c1083_pat.finditer(last_build_output):
        raw   = m.group(1).replace("\\", "/")
        bname = os.path.basename(raw).lower()

        if bname == "xxhash.h":
            xxhash_missing  = True
            has_any_luau_error = True
            continue
        if "openssl" in raw.lower():
            openssl_missing = True
            continue
        if bname.endswith((".h", ".hpp")):
            is_luau = (
                bname in _LUAU_HEADER_DIRS
                or "luau" in raw.lower()
                or bname in ("lua.h", "luaconf.h", "lualib.h", "luacode.h",
                             "luacodegen.h", "lapi.h", "ldo.h", "lgc.h",
                             "lobject.h", "lstate.h", "lstring.h", "ltable.h",
                             "lmem.h", "ldebug.h", "lfunc.h", "lvm.h")
            )
            if is_luau:
                luau_headers_missing.add(bname)
                has_any_luau_error = True
        elif bname.endswith(".cpp"):
            if bname in _LUAU_SOURCE_MAP or "luau" in raw.lower():
                luau_sources_missing.add(bname)
                has_any_luau_error = True

    if not has_any_luau_error and not last_build_output:
        log("[INFO] No missing Luau files detected in last build output.")
        log("[INFO] Run a build first so Build Doctor can read the C1083 lines.")
        return actions

    if not has_any_luau_error:
        log("[INFO] No Luau-specific C1083 errors found — use FIX INCS for other missing headers.")
        return actions

    if luau_headers_missing:
        log(f"[INFO] Missing Luau headers: {', '.join(sorted(luau_headers_missing))}", "warn")
    if luau_sources_missing:
        log(f"[INFO] Missing Luau source files: {', '.join(sorted(luau_sources_missing))}", "warn")
    if xxhash_missing:
        log("[INFO] Missing: xxhash.h (Luau extern dependency)", "warn")
    if openssl_missing:
        log("[WARN] Missing: openssl/err.h — OpenSSL is NOT part of Luau.", "warn")
        log("[WARN] Install OpenSSL separately: https://slproweb.com/products/Win32OpenSSL.html", "warn")
        log("[WARN] Then add its include path to your .vcxproj manually (or use FIX INCS).", "warn")

    # ── 2. Find git root ──────────────────────────────────────────────────────
    git_root = _find_git_root(folder)
    if not git_root:
        log("[WARN] Project is not inside a git repository — submodule init unavailable.", "warn")
        log("[INFO] Will attempt direct clone into project folder instead.", "info")
        # Use the project folder itself as the "root" for cloning purposes
        git_root = folder
    else:
        log(f"[INFO] Git root: {git_root}")

    # ── 3. Find existing Luau submodule dir (may be empty) ───────────────────
    luau_search_dirs = []
    for base in (git_root, folder):
        for sub in ("Luau", "luau", os.path.join("deps", "Luau"),
                    os.path.join("vendor", "luau"), os.path.join("extern", "luau"),
                    os.path.join("third_party", "luau"), os.path.join("ext", "Luau")):
            luau_search_dirs.append(os.path.join(base, sub))

    luau_dir = None
    for candidate in luau_search_dirs:
        if os.path.isdir(candidate):
            luau_dir = candidate
            log(f"[INFO] Found Luau directory: {luau_dir}")
            break

    # Check .gitmodules (only meaningful if this is a real git repo)
    gitmodules_path = os.path.join(git_root, ".gitmodules")
    submodule_path_from_modules = None
    _has_real_git = os.path.exists(os.path.join(git_root, ".git"))
    if _has_real_git and os.path.exists(gitmodules_path):
        with open(gitmodules_path, encoding="utf-8", errors="ignore") as f:
            gm = f.read()
        for block in re.split(r"\[submodule", gm):
            if "luau" in block.lower():
                pm = re.search(r"path\s*=\s*(.+)", block)
                if pm:
                    submodule_path_from_modules = os.path.join(
                        git_root, pm.group(1).strip().replace("/", os.sep)
                    )
                    if luau_dir is None:
                        luau_dir = submodule_path_from_modules
                    log(f"[INFO] .gitmodules Luau path: {submodule_path_from_modules}")
                    break

    # ── 4a. Submodule exists → init + update (only if real git repo) ─────────
    if _has_real_git and luau_dir and (submodule_path_from_modules or
                     os.path.exists(os.path.join(git_root, ".gitmodules"))):
        luau_populated = (
            os.path.isdir(luau_dir) and
            any(
                f.lower() in ("cmakelists.txt", "readme.md", "luau.h")
                for f in (os.listdir(luau_dir) if os.path.isdir(luau_dir) else [])
            )
        )
        if not luau_populated:
            log("[*] Luau submodule found but not initialized — running git submodule update...", "info")
            rc, out = _run_git(
                ["submodule", "update", "--init", "--recursive", "--progress"],
                cwd=git_root,
            )
            for line in out.splitlines():
                if line.strip():
                    cls = "error" if "error" in line.lower() or "fatal" in line.lower() else "dim"
                    log(f"    {line.rstrip()}", cls)
            if rc != 0:
                log(f"[ERROR] git submodule update failed (exit {rc}).", "error")
                log("[INFO] Try running manually: git submodule update --init --recursive", "warn")
                return actions
            log("[+] Submodule initialized successfully.", "info")
        else:
            log("[INFO] Luau submodule directory already populated.")

    # ── 4b. No submodule → clone directly ────────────────────────────────────
    elif luau_dir is None:
        luau_dir = os.path.join(git_root, "Luau")
        log(f"[*] No Luau submodule found — cloning {_LUAU_REPO} into {luau_dir} ...", "info")
        rc, out = _run_git(
            ["clone", "--depth=1", "--recurse-submodules", _LUAU_REPO, luau_dir],
            cwd=git_root,
            timeout=600,
        )
        for line in out.splitlines():
            if line.strip():
                cls = "error" if "error" in line.lower() or "fatal" in line.lower() else "dim"
                log(f"    {line.rstrip()}", cls)
        if rc != 0:
            log(f"[ERROR] git clone failed (exit {rc}).", "error")
            log(f"[INFO] Clone manually: git clone --depth=1 {_LUAU_REPO} Luau", "warn")
            return actions
        log("[+] Luau cloned successfully.", "info")

    # ── 5. Determine include directories to inject ───────────────────────────
    inc_dirs_to_add = set()

    # ── 5a. ALWAYS add the full set of Luau sub-package include dirs ──────────
    # These must ALL be present together — Compiler/include headers do
    # #include "../Includes/Ast.h" style relative refs that require Ast/include
    # to be a sibling on the include path.
    _LUAU_ALWAYS_INCS = [
        "include",           # top-level flat includes
        "VM/include",        # lua.h, luaconf.h, lualib.h, luacode.h
        "Compiler/include",  # luacode.h, luau_compile, BytecodeBuilder.h
        "Ast/include",       # Ast.h, Location.h, Lexer.h, Parser.h  ← REQUIRED peer
        "Bytecode/include",  # BytecodeUtils.h, Bytecode.h
        "Common/include",    # Common.h, StringUtils.h
    ]
    for sub in _LUAU_ALWAYS_INCS:
        d = os.path.join(luau_dir, sub.replace("/", os.sep))
        if os.path.isdir(d):
            inc_dirs_to_add.add(d)

    # Add sub-package include dirs for missing headers
    for hname in luau_headers_missing:
        sub = _LUAU_HEADER_DIRS.get(hname)
        if sub:
            candidate = os.path.join(luau_dir, sub.replace("/", os.sep))
            if os.path.isdir(candidate):
                inc_dirs_to_add.add(candidate)
        flat = os.path.join(luau_dir, "include")
        if os.path.isdir(flat):
            inc_dirs_to_add.add(flat)

    # Always add all include/ dirs under the repo for completeness
    for rootdir, subdirs, _ in os.walk(luau_dir):
        subdirs[:] = [d for d in subdirs if d.lower() not in
                      (".git", "x64", "debug", "release", "build", "tests", "bench")]
        if os.path.basename(rootdir).lower() == "include":
            inc_dirs_to_add.add(rootdir)

    # xxhash: find extern/xxhash inside Luau repo
    if xxhash_missing:
        for xd in _XXHASH_DIRS:
            xp = os.path.join(luau_dir, xd.replace("/", os.sep))
            if os.path.isdir(xp):
                inc_dirs_to_add.add(xp)
                log(f"[INFO] Found xxhash at: {xp}", "info")
                break
        else:
            log("[WARN] xxhash directory not found inside Luau repo — check extern/xxhash.", "warn")

    if not inc_dirs_to_add:
        log(f"[WARN] Could not find any include/ dirs under {luau_dir}.", "warn")
        return actions

    # ── 5b. Also find project-local Includes/ dirs (handles ../Includes/ style) ──
    # Walk the project folder for any directory named "Include", "Includes",
    # "inc", or "headers" and inject those too — catches hand-rolled include layouts.
    _LOCAL_INC_NAMES = {"include", "includes", "inc", "headers", "header"}
    for rootdir, subdirs, _ in os.walk(folder):
        subdirs[:] = [d for d in subdirs if d.lower() not in
                      (".git", ".vs", "x64", "x86", "debug", "release",
                       "build", "__pycache__", "luau")]
        bn = os.path.basename(rootdir).lower()
        if bn in _LOCAL_INC_NAMES:
            inc_dirs_to_add.add(rootdir)
            log(f"[INFO] Found local include dir: {rootdir}")

    for d in sorted(inc_dirs_to_add):
        log(f"[INFO] Will inject include path: {d}")

    # ── 6. Copy missing .cpp source files into project's src/Luau/ ───────────
    # IMPORTANT: Also copy the matching headers from the SAME clone into the
    # project's own Compiler/include/Luau/ so headers and .cpp are always the
    # same commit. Version mismatch between a stale header and a freshly cloned
    # .cpp causes C2051 (enum not constant), C2065 (undeclared members), and
    # C4003 (LUAU_FASTFLAGVARIABLE wrong arg count) — all seen in practice.
    copied_sources = []   # list of (dest_abs_path, rel_from_proj_dir)

    if luau_sources_missing:
        # Determine target directory: look for an existing src\Luau or src\luau,
        # or fall back to creating one next to the .vcxproj
        target_src_dir = None
        for rootdir, dirs, files in os.walk(folder):
            bn = os.path.basename(rootdir).lower()
            parent_bn = os.path.basename(os.path.dirname(rootdir)).lower()
            if bn == "luau" and parent_bn in ("src", "source", "sources"):
                target_src_dir = rootdir
                break
        if target_src_dir is None:
            # Create <project>/src/Luau/
            target_src_dir = os.path.join(folder, "src", "Luau")

        os.makedirs(target_src_dir, exist_ok=True)
        log(f"[INFO] Target source directory: {target_src_dir}", "info")

        for src_name in sorted(luau_sources_missing):
            dest = os.path.join(target_src_dir, src_name)
            if os.path.exists(dest):
                log(f"[OK] {src_name}: already present at {dest}")
                continue

            # Try to find the file in the Luau repo
            primary = _LUAU_SOURCE_MAP.get(src_name, "")
            alts    = _LUAU_SOURCE_ALT.get(src_name, [])
            src_abs = _find_file_in_repo(luau_dir, src_name, primary, alts)

            if src_abs:
                try:
                    shutil.copy2(src_abs, dest)
                    rel_to_folder = os.path.relpath(dest, folder)
                    copied_sources.append((dest, rel_to_folder))
                    log(f"[FIXED-SRC] Copied {src_name} → {rel_to_folder}", "info")
                except OSError as exc:
                    log(f"[ERROR] Could not copy {src_name}: {exc}", "error")
            else:
                log(f"[WARN] {src_name} not found in Luau repo — may need manual copy.", "warn")

    # ── 6b. Sync Luau headers into project's Compiler/include/Luau/ ──────────
    # If we copied any .cpp files, also copy the headers from the SAME clone
    # so the project is guaranteed to use matching header/source versions.
    # This prevents C2051 / C2065 / C4003 version-mismatch errors.
    if luau_sources_missing or luau_headers_missing:
        log("[*] Syncing Luau headers from cloned repo into project...", "info")
        # Map: sub-package include dir in repo → dest dir in project
        # We place them under <folder>/Compiler/include/Luau/ mirroring the
        # standard Luau flat layout expected by BytecodeBuilder.h etc.
        _HDR_SYNC_SUBDIRS = [
            ("Compiler/include/Luau", os.path.join(folder, "Compiler", "include", "Luau")),
            ("Ast/include/Luau",      os.path.join(folder, "Ast",      "include", "Luau")),
            ("VM/include",            os.path.join(folder, "VM",       "include")),
            ("Bytecode/include/Luau", os.path.join(folder, "Bytecode", "include", "Luau")),
            ("Common/include/Luau",   os.path.join(folder, "Common",   "include", "Luau")),
        ]
        for repo_sub, dest_dir in _HDR_SYNC_SUBDIRS:
            repo_src_dir = os.path.join(luau_dir, repo_sub.replace("/", os.sep))
            if not os.path.isdir(repo_src_dir):
                continue
            os.makedirs(dest_dir, exist_ok=True)
            for hfile in os.listdir(repo_src_dir):
                if not hfile.lower().endswith((".h", ".hpp")):
                    continue
                src_h  = os.path.join(repo_src_dir, hfile)
                dest_h = os.path.join(dest_dir, hfile)
                try:
                    # Always overwrite — ensure version consistency
                    shutil.copy2(src_h, dest_h)
                    log(f"[FIXED-HDR] Synced {hfile} → {os.path.relpath(dest_h, folder)}", "info")
                except OSError as exc:
                    log(f"[WARN] Could not sync header {hfile}: {exc}", "warn")
        # After syncing, also add the project-local header dirs to inc_dirs_to_add
        for _, dest_dir in _HDR_SYNC_SUBDIRS:
            parent = os.path.dirname(dest_dir)  # e.g. Compiler/include
            if os.path.isdir(parent):
                inc_dirs_to_add.add(parent)
            if os.path.isdir(dest_dir):
                inc_dirs_to_add.add(dest_dir)
    all_vcxproj = []
    for rootdir, _, files in os.walk(folder):
        for fname in files:
            if fname.lower().endswith(".vcxproj"):
                all_vcxproj.append(os.path.join(rootdir, fname))

    for vcxproj_path in all_vcxproj:
        proj_dir = os.path.dirname(vcxproj_path)
        fname    = os.path.basename(vcxproj_path)
        try:
            tree = ET.parse(vcxproj_path)
            root = tree.getroot()
        except ET.ParseError as exc:
            log(f"[SKIP] {fname}: XML parse error — {exc}", "warn")
            continue

        ns_m  = re.match(r"^\{(.*?)\}", root.tag)
        xmlns = ns_m.group(1) if ns_m else ""

        def tag(name):
            return f"{{{xmlns}}}{name}" if xmlns else name

        proj_changed = False

        # 7a. Inject include directories ──────────────────────────────────────
        existing_inc = set()
        for el in root.iter(tag("AdditionalIncludeDirectories")):
            for part in re.split(r"[;,]", el.text or ""):
                part = part.strip()
                if part and "%" not in part:
                    existing_inc.add(os.path.normpath(os.path.join(proj_dir, part)).lower())

        new_rel = []
        for inc_abs in sorted(inc_dirs_to_add):
            norm_abs = os.path.normpath(inc_abs).lower()
            if norm_abs not in existing_inc:
                try:
                    rel = os.path.relpath(inc_abs, proj_dir)
                except ValueError:
                    rel = inc_abs
                # Also guard against the relative form already being present
                norm_rel = os.path.normpath(os.path.join(proj_dir, rel)).lower()
                if norm_rel not in existing_inc:
                    new_rel.append(rel)
                    existing_inc.add(norm_rel)   # prevent adding the same dir twice

        if new_rel:
            new_str = ";".join(new_rel) + ";%(AdditionalIncludeDirectories)"
            updated = 0
            for idg in root.iter(tag("ItemDefinitionGroup")):
                for clc in idg.iter(tag("ClCompile")):
                    aid = clc.find(tag("AdditionalIncludeDirectories"))
                    if aid is not None:
                        existing_text = (aid.text or "").replace(
                            ";%(AdditionalIncludeDirectories)", ""
                        ).rstrip(";")
                        parts = [p for p in existing_text.split(";") if p.strip()]
                        # Build a normalised set of what's already listed
                        already_norm = {
                            os.path.normpath(os.path.join(proj_dir, p)).lower()
                            for p in parts if p.strip()
                        }
                        for rel in new_rel:
                            norm = os.path.normpath(os.path.join(proj_dir, rel)).lower()
                            if norm not in already_norm:
                                parts.append(rel)
                                already_norm.add(norm)
                        aid.text = ";".join(parts) + ";%(AdditionalIncludeDirectories)"
                    else:
                        el      = ET.SubElement(clc, tag("AdditionalIncludeDirectories"))
                        el.text = new_str
                    updated += 1
            if updated == 0:
                idg = ET.SubElement(root, tag("ItemDefinitionGroup"))
                clc = ET.SubElement(idg, tag("ClCompile"))
                el  = ET.SubElement(clc, tag("AdditionalIncludeDirectories"))
                el.text = new_str
            proj_changed = True
            log(f"[FIXED-LUAU] {fname}: injected {len(new_rel)} include path(s)", "info")
        else:
            log(f"[OK] {fname}: Luau include paths already present.")

        # 7b. Inject ClCompile entries for newly copied .cpp files ────────────
        if copied_sources:
            # Collect already-referenced .cpp files
            existing_compile = set()
            for el in root.iter(tag("ClCompile")):
                inc_attr = el.attrib.get("Include", "")
                if inc_attr:
                    existing_compile.add(
                        os.path.normpath(os.path.join(proj_dir, inc_attr.replace("/", os.sep))).lower()
                    )

            srcs_to_add = []
            for dest_abs, rel_to_folder in copied_sources:
                abs_norm = os.path.normpath(dest_abs).lower()
                if abs_norm not in existing_compile:
                    try:
                        rel_from_proj = os.path.relpath(dest_abs, proj_dir)
                    except ValueError:
                        rel_from_proj = dest_abs
                    srcs_to_add.append(rel_from_proj)

            if srcs_to_add:
                # Find or create an ItemGroup for source files
                src_ig = None
                for ig in root.iter(tag("ItemGroup")):
                    if ig.find(tag("ClCompile")) is not None:
                        src_ig = ig
                        break
                if src_ig is None:
                    src_ig = ET.SubElement(root, tag("ItemGroup"))

                for rel_src in srcs_to_add:
                    el = ET.SubElement(src_ig, tag("ClCompile"))
                    el.attrib["Include"] = rel_src
                    log(f"[FIXED-SRC] {fname}: added ClCompile → {rel_src}", "info")

                proj_changed = True

        if proj_changed:
            if xmlns:
                ET.register_namespace("", xmlns)
            tree.write(vcxproj_path, encoding="utf-8", xml_declaration=True)

    if openssl_missing:
        log("", "dim")
        log("[!] OpenSSL (openssl/err.h) still requires manual install:", "warn")
        log("    → https://slproweb.com/products/Win32OpenSSL.html", "warn")
        log("    → After installing, add its include path via FIX INCS or Settings.", "warn")

    return actions


# ─────────────────────────────────────────────
# Auto-Fix Pass 8: BOM Cleanup in .cs files
# ─────────────────────────────────────────────

def fix_cs_bom(folder):
    """
    Strips UTF-8 BOM (\\xef\\xbb\\xbf) from the beginning of .cs files.
    A BOM in the middle of a C# compilation unit can cause CS1513 or
    'unexpected character' errors in some SDK/CLI scenarios.
    Returns a list of action strings.
    """
    actions    = []
    BOM        = "\ufeff"
    cs_found   = 0

    for rootdir, _, files in os.walk(folder):
        for fname in files:
            if not fname.lower().endswith(".cs"):
                continue
            full    = os.path.join(rootdir, fname)
            rel     = os.path.relpath(full, folder).replace("\\", "/")
            cs_found += 1
            try:
                with open(full, "r", encoding="utf-8-sig", errors="ignore") as f:
                    content = f.read()
                # utf-8-sig automatically strips BOM on read
                # Check if raw bytes start with BOM
                with open(full, "rb") as fb:
                    raw_start = fb.read(3)
                if raw_start == b"\xef\xbb\xbf":
                    with open(full, "w", encoding="utf-8") as f:
                        f.write(content)
                    actions.append(f"[FIXED-BOM] {rel}: removed UTF-8 BOM.")
            except OSError as exc:
                actions.append(f"[ERROR] {rel}: {exc}")

    if cs_found == 0:
        actions.append("No .cs files found.")
    elif not any(a.startswith("[FIXED-BOM]") for a in actions):
        actions.append(f"[OK] Checked {cs_found} .cs file(s): no BOM issues found.")

    return actions


# ─────────────────────────────────────────────
# Framework Consistency Check
# ─────────────────────────────────────────────

def check_framework_consistency(folder):
    """
    Scans .csproj files and extracts <TargetFramework> / <TargetFrameworks>.
    Returns {"frameworks": {name:[fw,...]}, "mixed": bool, "warnings": [str]}
    """
    result = {"frameworks": {}, "mixed": False, "warnings": []}

    all_csproj = []
    for rootdir, _, files in os.walk(folder):
        for fname in files:
            if fname.lower().endswith(".csproj"):
                all_csproj.append(os.path.join(rootdir, fname))

    if not all_csproj:
        return result

    fw_set = set()
    for cp in all_csproj:
        fname = os.path.basename(cp)
        try:
            tree, root, xmlns = _parse_csproj(cp)
        except Exception:
            continue

        frameworks = []
        for tag_name in ("TargetFramework", "TargetFrameworks"):
            for el in root.iter(_tag(xmlns, tag_name)):
                if el.text:
                    for fw in el.text.split(";"):
                        fw = fw.strip()
                        if fw:
                            frameworks.append(fw)
                            fw_set.add(fw)

        if frameworks:
            result["frameworks"][fname] = frameworks

    legacy = {f for f in fw_set if re.match(r"^net[1-4]\d*$", f, re.IGNORECASE)}
    modern = {f for f in fw_set if re.match(
        r"^net[5-9]|^net\d{2}|netcoreapp|netstandard", f, re.IGNORECASE)}

    if legacy and modern:
        result["mixed"] = True
        result["warnings"].append(
            f"Mixed target frameworks: legacy ({', '.join(sorted(legacy))}) "
            f"vs modern ({', '.join(sorted(modern))}). This can cause "
            "reference and runtime compatibility errors."
        )
    elif len(fw_set) > 1:
        result["warnings"].append(
            f"Multiple target frameworks across projects: {', '.join(sorted(fw_set))}. "
            "Verify all ProjectReferences are compatible."
        )

    return result


# ─────────────────────────────────────────────
# NuGet / dotnet restore
# ─────────────────────────────────────────────

def _run_dotnet_restore_cmd(folder):
    """
    Runs 'dotnet restore' in the given folder.
    Returns {"ok": bool, "output": str, "returncode": int}
    """
    try:
        proc = subprocess.run(
            ["dotnet", "restore"],
            cwd=folder,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=180
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        return {"ok": proc.returncode == 0, "output": combined, "returncode": proc.returncode}
    except FileNotFoundError:
        return {
            "ok": False,
            "output": "'dotnet' is not recognized — install the .NET SDK and add it to PATH.",
            "returncode": -1
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "output": "dotnet restore timed out after 180 seconds.",
            "returncode": -1
        }
    except Exception as exc:
        return {"ok": False, "output": str(exc), "returncode": -1}


# ─────────────────────────────────────────────
# Fix-All orchestrator (C# projects)
# ─────────────────────────────────────────────

def fix_all_cs(folder):
    """
    Runs all C# / csproj auto-fix passes in order:
      Pass 1 & 2 — Dead Compile refs + Missing ProjectReferences
      Pass 3     — Duplicate references + Empty ItemGroups
      Pass 4     — Missing AssemblyName / RootNamespace
      Pass 5     — C# BOM cleanup
    Returns a combined action list.
    """
    result = []
    result.append("=== Pass 1 & 2: Dead Compile Refs + Missing ProjectRefs ===")
    result.extend(fix_missing_cs_files(folder))
    result.append("")
    result.append("=== Pass 3: Duplicate References + Empty ItemGroups ===")
    result.extend(fix_duplicate_refs(folder))
    result.append("")
    result.append("=== Pass 4: AssemblyName / RootNamespace ===")
    result.extend(fix_assembly_metadata(folder))
    result.append("")
    result.append("=== Pass 5: UTF-8 BOM Cleanup ===")
    result.extend(fix_cs_bom(folder))
    return result


# ─────────────────────────────────────────────
# Auto-Fix Pass 9: Missing ClCompile / ClInclude in .vcxproj
# ─────────────────────────────────────────────

def fix_vcxproj_missing_items(folder):
    """
    Scans every .vcxproj under `folder` and compares the files it references
    against the files that physically exist on disk.

    For EACH .vcxproj this pass will:
      A) Find every .cpp / .c file on disk (under the project folder) that is
         NOT already listed as a <ClCompile Include="..."/> entry — and add it.
      B) Find every .h / .hpp file on disk that is NOT already listed as a
         <ClInclude Include="..."/> entry — and add it.
      C) Remove any <ClCompile> or <ClInclude> entries whose path resolves to a
         file that no longer exists on disk (dead references).

    Skips output/cache directories (x64, x86, Debug, Release, .vs, .git,
    __pycache__, RelWithDebInfo, MinSizeRel, build, CMakeFiles).

    Returns a list of human-readable action strings.
    """
    import xml.etree.ElementTree as ET

    actions = []

    # Directories that are build-output or tooling artefacts — never source
    _SKIP_DIRS = {
        "x64", "x86", "debug", "release", "relwithdebinfo", "minsizerel",
        ".vs", ".git", "__pycache__", "build", "cmake_install",
        "cmakefiles", "ipch", ".cache",
    }

    all_vcxproj = []
    for rootdir, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d.lower() not in _SKIP_DIRS]
        for fname in files:
            if fname.lower().endswith(".vcxproj"):
                all_vcxproj.append(os.path.join(rootdir, fname))

    if not all_vcxproj:
        actions.append("[SKIP] No .vcxproj files found under the selected folder.")
        return actions

    for vcxproj_path in all_vcxproj:
        proj_dir = os.path.dirname(vcxproj_path)
        fname    = os.path.basename(vcxproj_path)

        try:
            tree = ET.parse(vcxproj_path)
            root = tree.getroot()
        except ET.ParseError as exc:
            actions.append(f"[SKIP] {fname}: XML parse error — {exc}")
            continue

        ns_m  = re.match(r"^\{(.*?)\}", root.tag)
        xmlns = ns_m.group(1) if ns_m else ""

        def _tag(name):
            return f"{{{xmlns}}}{name}" if xmlns else name

        def _strip(tag):
            return re.sub(r"^\{.*?\}", "", tag)

        # ── Collect what the project already references ───────────────────────
        # Normalised absolute paths already in the project file
        existing_compile  = {}   # norm_abs_lower -> element
        existing_include  = {}   # norm_abs_lower -> element
        dead_compile      = []   # (parent_ig, element, display_path)
        dead_include      = []

        for ig in root.iter(_tag("ItemGroup")):
            for child in list(ig):
                tag_local = _strip(child.tag)
                inc_attr  = child.attrib.get("Include", "")
                if not inc_attr:
                    continue
                abs_path  = os.path.normpath(
                    os.path.join(proj_dir, inc_attr.replace("/", os.sep))
                )
                norm      = abs_path.lower()
                if tag_local == "ClCompile":
                    if os.path.isfile(abs_path):
                        existing_compile[norm] = child
                    else:
                        dead_compile.append((ig, child, inc_attr))
                elif tag_local == "ClInclude":
                    if os.path.isfile(abs_path):
                        existing_include[norm] = child
                    else:
                        dead_include.append((ig, child, inc_attr))

        # ── Walk disk for all source/header files ─────────────────────────────
        disk_sources  = {}   # norm_abs_lower -> abs_path
        disk_headers  = {}

        for rootdir2, dirs2, files2 in os.walk(proj_dir):
            dirs2[:] = [d for d in dirs2 if d.lower() not in _SKIP_DIRS]
            for f in files2:
                abs_f = os.path.join(rootdir2, f)
                norm  = abs_f.lower()
                lo    = f.lower()
                if lo.endswith((".cpp", ".c", ".cxx", ".cc")):
                    disk_sources[norm] = abs_f
                elif lo.endswith((".h", ".hpp", ".hxx", ".hh")):
                    disk_headers[norm] = abs_f

        # ── Compute what needs to be added ────────────────────────────────────
        missing_sources = {
            n: p for n, p in disk_sources.items()
            if n not in existing_compile
        }
        missing_headers = {
            n: p for n, p in disk_headers.items()
            if n not in existing_include
        }

        changed = False

        # ── A) Remove dead <ClCompile> entries ────────────────────────────────
        for ig, child, disp in dead_compile:
            ig.remove(child)
            changed = True
            actions.append(f"[FIXED-DEAD] {fname}: removed dead <ClCompile Include=\"{disp}\" />")

        # ── B) Remove dead <ClInclude> entries ────────────────────────────────
        for ig, child, disp in dead_include:
            ig.remove(child)
            changed = True
            actions.append(f"[FIXED-DEAD] {fname}: removed dead <ClInclude Include=\"{disp}\" />")

        # ── C) Add missing <ClCompile> entries ────────────────────────────────
        if missing_sources:
            # Find existing ClCompile ItemGroup or create one
            src_ig = None
            for ig in root.iter(_tag("ItemGroup")):
                for child in ig:
                    if _strip(child.tag) == "ClCompile":
                        src_ig = ig
                        break
                if src_ig is not None:
                    break
            if src_ig is None:
                src_ig = ET.SubElement(root, _tag("ItemGroup"))

            for norm, abs_p in sorted(missing_sources.items()):
                try:
                    rel = os.path.relpath(abs_p, proj_dir)
                except ValueError:
                    rel = abs_p   # different drive — keep absolute
                el = ET.SubElement(src_ig, _tag("ClCompile"))
                el.set("Include", rel)
                changed = True
                actions.append(f"[FIXED-ADD] {fname}: added <ClCompile Include=\"{rel}\" />")

        # ── D) Add missing <ClInclude> entries ────────────────────────────────
        if missing_headers:
            # Find existing ClInclude ItemGroup or create one
            hdr_ig = None
            for ig in root.iter(_tag("ItemGroup")):
                for child in ig:
                    if _strip(child.tag) == "ClInclude":
                        hdr_ig = ig
                        break
                if hdr_ig is not None:
                    break
            if hdr_ig is None:
                hdr_ig = ET.SubElement(root, _tag("ItemGroup"))

            for norm, abs_p in sorted(missing_headers.items()):
                try:
                    rel = os.path.relpath(abs_p, proj_dir)
                except ValueError:
                    rel = abs_p
                el = ET.SubElement(hdr_ig, _tag("ClInclude"))
                el.set("Include", rel)
                changed = True
                actions.append(f"[FIXED-ADD] {fname}: added <ClInclude Include=\"{rel}\" />")

        # ── Write back if modified ────────────────────────────────────────────
        if changed:
            if xmlns:
                ET.register_namespace("", xmlns)
            tree.write(vcxproj_path, encoding="utf-8", xml_declaration=True)
        else:
            total = len(existing_compile) + len(existing_include)
            actions.append(
                f"[OK] {fname}: all {total} source/header reference(s) already present and valid."
            )

    return actions


# ─────────────────────────────────────────────
# Auto-Fix Pass 10: Missing .lib Files (LNK2001/LNK2019/LNK1181)
# ─────────────────────────────────────────────

def fix_missing_libs(folder, last_build_output=""):
    """
    Scans build output for LNK2001/LNK2019/LNK1181 unresolved symbol errors,
    attempts to find matching .lib files anywhere in the project tree or common
    SDK locations, and injects them into AdditionalDependencies / AdditionalLibraryDirectories
    in every .vcxproj found.

    Also does a blanket scan: any .lib found under the project folder whose
    directory is not already in the vcxproj lib paths gets added.

    Returns a list of action strings.
    """
    import xml.etree.ElementTree as ET

    actions = []

    # ── Step 1: extract referenced symbol names from LNK errors ──────────────
    # LNK2001/LNK2019: "unresolved external symbol __imp_SomeFunc"
    # LNK1181: "cannot open input file 'foo.lib'"
    lnk_sym_pat   = re.compile(r"(?:LNK2001|LNK2019)[^\n]*?symbol\s+\"([^\"]+)\"", re.IGNORECASE)
    lnk1181_pat   = re.compile(r"LNK1181[^\n]*?'([^']+\.lib)'", re.IGNORECASE)
    lnk_open_pat  = re.compile(r"cannot open file[^\n]*?'([^']+\.lib)'", re.IGNORECASE)

    explicit_libs = set()   # .lib filenames named directly in errors
    for m in lnk1181_pat.finditer(last_build_output):
        explicit_libs.add(os.path.basename(m.group(1)).lower())
    for m in lnk_open_pat.finditer(last_build_output):
        explicit_libs.add(os.path.basename(m.group(1)).lower())

    has_lnk = bool(
        re.search(r"LNK2001|LNK2019|LNK1181|LNK1120|LNK1104", last_build_output, re.IGNORECASE)
    )

    if not has_lnk and not last_build_output:
        actions.append("[INFO] No LNK errors detected — running blanket .lib scan instead.")
    elif not has_lnk:
        actions.append("[INFO] No LNK errors found in last build output.")
        return actions

    if explicit_libs:
        actions.append(f"[INFO] Libs named in errors: {', '.join(sorted(explicit_libs))}")

    # ── Step 2: find all .vcxproj ────────────────────────────────────────────
    all_vcxproj = []
    for rootdir, _, files in os.walk(folder):
        for fname in files:
            if fname.lower().endswith(".vcxproj"):
                all_vcxproj.append(os.path.join(rootdir, fname))

    if not all_vcxproj:
        actions.append("[SKIP] No .vcxproj files found.")
        return actions

    # ── Step 3: build map of all .lib files in the project tree ──────────────
    _SKIP_DIRS = {"x64", "x86", "debug", "release", ".git", ".vs",
                  "__pycache__", "relwithdebinfo", "minsizerel"}
    lib_dir_map  = {}   # libname.lower() -> set of abs dir paths
    for rootdir, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d.lower() not in _SKIP_DIRS]
        for f in files:
            if f.lower().endswith(".lib"):
                lib_dir_map.setdefault(f.lower(), set()).add(rootdir)

    # Also search common Windows SDK / VC lib paths
    sdk_lib_roots = []
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        pf = os.environ.get(env, "")
        if pf:
            sdk_lib_roots += [
                os.path.join(pf, "Windows Kits", "10", "Lib"),
                os.path.join(pf, "Microsoft Visual Studio"),
            ]
    for root in sdk_lib_roots:
        if not os.path.isdir(root):
            continue
        for rootdir, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d.lower() not in _SKIP_DIRS]
            for f in files:
                if f.lower().endswith(".lib"):
                    lib_dir_map.setdefault(f.lower(), set()).add(rootdir)

    # ── Step 4: resolve directories to inject ────────────────────────────────
    needed_lib_dirs  = set()
    needed_lib_names = set()

    if explicit_libs:
        for lname in explicit_libs:
            found = lib_dir_map.get(lname, set())
            if found:
                for d in found:
                    needed_lib_dirs.add(d)
                    needed_lib_names.add(lname)
                    actions.append(f"[FOUND] '{lname}' → {d}")
            else:
                actions.append(f"[WARN] '{lname}' not found in project tree or SDK paths.")
    else:
        # Blanket: collect every dir that has .lib files under the project folder
        for dirs in lib_dir_map.values():
            for d in dirs:
                if d.startswith(folder):
                    needed_lib_dirs.add(d)

    if not needed_lib_dirs:
        actions.append("[OK] No new lib directories to inject.")
        return actions

    # ── Step 5: patch each .vcxproj ──────────────────────────────────────────
    for vcxproj_path in all_vcxproj:
        proj_dir = os.path.dirname(vcxproj_path)
        fname    = os.path.basename(vcxproj_path)

        try:
            tree = ET.parse(vcxproj_path)
            root = tree.getroot()
        except ET.ParseError as exc:
            actions.append(f"[SKIP] {fname}: XML parse error — {exc}")
            continue

        ns_m  = re.match(r"^\{(.*?)\}", root.tag)
        xmlns = ns_m.group(1) if ns_m else ""

        def _t(name):
            return f"{{{xmlns}}}{name}" if xmlns else name

        # Gather existing lib dirs
        existing_lib_dirs = set()
        for el in root.iter(_t("AdditionalLibraryDirectories")):
            for part in re.split(r"[;,]", el.text or ""):
                part = part.strip()
                if part and "%" not in part:
                    existing_lib_dirs.add(
                        os.path.normpath(os.path.join(proj_dir, part)).lower()
                    )

        new_lib_rels = []
        for d in sorted(needed_lib_dirs):
            norm = os.path.normpath(d).lower()
            if norm not in existing_lib_dirs:
                try:
                    rel = os.path.relpath(d, proj_dir)
                except ValueError:
                    rel = d
                new_lib_rels.append(rel)
                existing_lib_dirs.add(norm)

        # Gather existing AdditionalDependencies
        existing_dep_names = set()
        for el in root.iter(_t("AdditionalDependencies")):
            for part in re.split(r"[;,]", el.text or ""):
                existing_dep_names.add(part.strip().lower())

        new_dep_names = [
            n for n in sorted(needed_lib_names)
            if n.lower() not in existing_dep_names
        ]

        changed = False

        for idg in root.iter(_t("ItemDefinitionGroup")):
            link_el = idg.find(_t("Link"))
            if link_el is None:
                link_el = ET.SubElement(idg, _t("Link"))

            if new_lib_rels:
                ald = link_el.find(_t("AdditionalLibraryDirectories"))
                if ald is None:
                    ald = ET.SubElement(link_el, _t("AdditionalLibraryDirectories"))
                    ald.text = ""
                existing_text = (ald.text or "").replace(
                    ";%(AdditionalLibraryDirectories)", ""
                ).rstrip(";")
                parts = [p for p in existing_text.split(";") if p.strip()]
                for rel in new_lib_rels:
                    norm = os.path.normpath(os.path.join(proj_dir, rel)).lower()
                    if norm not in {os.path.normpath(os.path.join(proj_dir, p)).lower() for p in parts}:
                        parts.append(rel)
                ald.text = ";".join(parts) + ";%(AdditionalLibraryDirectories)"
                changed = True

            if new_dep_names:
                ad = link_el.find(_t("AdditionalDependencies"))
                if ad is None:
                    ad = ET.SubElement(link_el, _t("AdditionalDependencies"))
                    ad.text = ""
                existing_text = (ad.text or "").replace(
                    ";%(AdditionalDependencies)", ""
                ).rstrip(";")
                parts = [p for p in existing_text.split(";") if p.strip()]
                for dep in new_dep_names:
                    if dep not in {p.lower() for p in parts}:
                        parts.append(dep)
                ad.text = ";".join(parts) + ";%(AdditionalDependencies)"
                changed = True

        if changed:
            if xmlns:
                ET.register_namespace("", xmlns)
            tree.write(vcxproj_path, encoding="utf-8", xml_declaration=True)
            if new_lib_rels:
                actions.append(f"[FIXED-LIB] {fname}: injected {len(new_lib_rels)} lib path(s) → {', '.join(new_lib_rels)}")
            if new_dep_names:
                actions.append(f"[FIXED-LIB] {fname}: added {len(new_dep_names)} lib(s) to AdditionalDependencies → {', '.join(new_dep_names)}")
        else:
            actions.append(f"[OK] {fname}: all required lib paths already present.")

    return actions


# ─────────────────────────────────────────────
# Auto-Fix Pass 11: Precompiled Header (PCH) Auto-Stub
# ─────────────────────────────────────────────

def fix_pch(folder, last_build_output=""):
    """
    Handles C1010: 'unexpected end of file while looking for precompiled header'.

    Strategy A — If the project has UsePrecompiledHeader=Use in its .vcxproj but
                  the pch.h / stdafx.h file doesn't exist, creates a minimal stub.
    Strategy B — If no PCH is configured but C1010 fires anyway, DISABLES PCH in
                  the .vcxproj (sets PrecompiledHeader to NotUsing).

    Returns a list of action strings.
    """
    import xml.etree.ElementTree as ET

    actions = []

    has_c1010 = bool(re.search(r"error C1010", last_build_output, re.IGNORECASE))
    if not has_c1010 and last_build_output:
        actions.append("[INFO] No C1010 errors detected — PCH looks fine.")
        return actions

    all_vcxproj = []
    for rootdir, _, files in os.walk(folder):
        for fname in files:
            if fname.lower().endswith(".vcxproj"):
                all_vcxproj.append(os.path.join(rootdir, fname))

    if not all_vcxproj:
        actions.append("[SKIP] No .vcxproj files found.")
        return actions

    for vcxproj_path in all_vcxproj:
        proj_dir = os.path.dirname(vcxproj_path)
        fname    = os.path.basename(vcxproj_path)

        try:
            tree = ET.parse(vcxproj_path)
            root = tree.getroot()
        except ET.ParseError as exc:
            actions.append(f"[SKIP] {fname}: XML parse error — {exc}")
            continue

        ns_m  = re.match(r"^\{(.*?)\}", root.tag)
        xmlns = ns_m.group(1) if ns_m else ""

        def _t(name):
            return f"{{{xmlns}}}{name}" if xmlns else name

        changed = False

        # Find PrecompiledHeader settings
        pch_use   = None   # element with Use/Create/NotUsing
        pch_file  = None   # element with pch header filename
        pch_filename = "pch.h"

        for idg in root.iter(_t("ItemDefinitionGroup")):
            clc = idg.find(_t("ClCompile"))
            if clc is not None:
                el = clc.find(_t("PrecompiledHeader"))
                if el is not None:
                    pch_use = el
                el2 = clc.find(_t("PrecompiledHeaderFile"))
                if el2 is not None and el2.text:
                    pch_file = el2
                    pch_filename = el2.text.strip()

        pch_abs = os.path.join(proj_dir, pch_filename)

        if pch_use is not None and (pch_use.text or "").strip().lower() == "use":
            # PCH is configured as "Use" — check if the header exists
            if not os.path.exists(pch_abs):
                # Create a minimal stub
                stub_content = (
                    "#pragma once\n"
                    "// Auto-generated precompiled header stub by Build Doctor\n"
                    "// Add your commonly-used headers here.\n\n"
                    "#include <windows.h>\n"
                    "#include <string>\n"
                    "#include <vector>\n"
                    "#include <memory>\n"
                )
                try:
                    with open(pch_abs, "w", encoding="utf-8") as f:
                        f.write(stub_content)
                    actions.append(f"[FIXED-PCH] Created stub PCH file: {pch_filename}")
                    changed = True
                except OSError as exc:
                    actions.append(f"[ERROR] Could not create {pch_filename}: {exc}")
            else:
                actions.append(f"[OK] {fname}: PCH file '{pch_filename}' already exists.")
        elif pch_use is not None and (pch_use.text or "").strip().lower() in ("", "notusing"):
            actions.append(f"[OK] {fname}: PCH already disabled (NotUsing).")
        else:
            # No PCH config at all, or C1010 fired — disable PCH globally
            for idg in root.iter(_t("ItemDefinitionGroup")):
                clc = idg.find(_t("ClCompile"))
                if clc is not None:
                    el = clc.find(_t("PrecompiledHeader"))
                    if el is None:
                        el = ET.SubElement(clc, _t("PrecompiledHeader"))
                    el.text = "NotUsing"
                    changed = True

            if changed:
                actions.append(f"[FIXED-PCH] {fname}: disabled PCH (set NotUsing on all ClCompile groups).")
            else:
                actions.append(f"[OK] {fname}: no PCH configuration found — no change needed.")

        if changed:
            if xmlns:
                ET.register_namespace("", xmlns)
            tree.write(vcxproj_path, encoding="utf-8", xml_declaration=True)

    return actions


# ─────────────────────────────────────────────
# Auto-Fix Pass 12: ZSTD / OpenSSL / C4101 via vcpkg
# ─────────────────────────────────────────────

# Packages Build Doctor knows how to install via vcpkg
# Maps a detection regex → vcpkg package name(s) + human label
_VCPKG_PACKAGE_MAP = [
    # (header/symbol pattern,  vcpkg_triplet_pkg,    display_name)
    (r"ZSTD_decompress|zstd(?:\.h|/zstd\.h)|error C3861.*zstd",
     "zstd:x64-windows-static", "ZSTD"),
    (r"openssl/err\.h|openssl/ssl\.h|openssl/evp\.h|openssl/bio\.h|OPENSSL_",
     "openssl:x64-windows-static", "OpenSSL"),
    (r"error C1083[^']*'[^']*zstd",
     "zstd:x64-windows-static", "ZSTD"),
    (r"error C1083[^']*'[^']*openssl",
     "openssl:x64-windows-static", "OpenSSL"),
]

def _find_vcpkg():
    """
    Locate vcpkg.exe.  Search order:
      1. VCPKG_ROOT env var
      2. Common install locations
      3. PATH
    Returns absolute path to vcpkg.exe or None.
    """
    # Env var set by many CI systems and the official vcpkg install guide
    vcpkg_root = os.environ.get("VCPKG_ROOT", "")
    if vcpkg_root:
        candidate = os.path.join(vcpkg_root, "vcpkg.exe")
        if os.path.isfile(candidate):
            return candidate

    # Common manual install locations
    common_roots = []
    for env in ("SystemDrive", "HOMEDRIVE"):
        drive = os.environ.get(env, "C:")
        common_roots += [
            os.path.join(drive, os.sep, "vcpkg", "vcpkg.exe"),
            os.path.join(drive, os.sep, "tools", "vcpkg", "vcpkg.exe"),
            os.path.join(drive, os.sep, "src", "vcpkg", "vcpkg.exe"),
        ]
    for pf_env in ("ProgramFiles", "ProgramFiles(x86)"):
        pf = os.environ.get(pf_env, "")
        if pf:
            common_roots.append(os.path.join(pf, "vcpkg", "vcpkg.exe"))

    # Also check next to the project root (some devs vendor vcpkg in repo)
    for candidate in common_roots:
        if os.path.isfile(candidate):
            return candidate

    # Last resort: PATH
    try:
        result = subprocess.check_output(
            ["where", "vcpkg"], text=True, stderr=subprocess.DEVNULL
        ).strip().splitlines()
        if result:
            return result[0].strip()
    except Exception:
        pass

    return None


def _vcpkg_installed_root(vcpkg_exe):
    """
    Return the vcpkg installed/ directory so we can add include/lib paths.
    Tries `vcpkg fetch` style env, then derives from executable location.
    """
    vcpkg_dir = os.path.dirname(vcpkg_exe)
    installed  = os.path.join(vcpkg_dir, "installed")
    if os.path.isdir(installed):
        return installed
    return None


def _inject_vcpkg_paths(folder, vcpkg_installed_root, packages, actions):
    """
    After vcpkg installs packages, find their include/ and lib/ directories
    and inject them into every .vcxproj under `folder`.
    """
    import xml.etree.ElementTree as ET

    if not vcpkg_installed_root or not os.path.isdir(vcpkg_installed_root):
        actions.append("[WARN] Cannot locate vcpkg installed/ directory — add paths manually.")
        return

    # Collect include dirs and lib dirs for all installed triplets
    new_inc_dirs = set()
    new_lib_dirs = set()
    new_libs     = set()

    for entry in os.listdir(vcpkg_installed_root):
        triplet_dir = os.path.join(vcpkg_installed_root, entry)
        if not os.path.isdir(triplet_dir):
            continue
        inc = os.path.join(triplet_dir, "include")
        lib = os.path.join(triplet_dir, "lib")
        if os.path.isdir(inc):
            new_inc_dirs.add(inc)
        if os.path.isdir(lib):
            new_lib_dirs.add(lib)
            # Collect .lib files for AdditionalDependencies
            for f in os.listdir(lib):
                if f.lower().endswith(".lib") and not f.lower().startswith("zlib"):
                    new_libs.add(f)

    if not new_inc_dirs and not new_lib_dirs:
        actions.append("[WARN] vcpkg installed/ found but no include/lib dirs detected.")
        return

    all_vcxproj = []
    for rootdir, _, files in os.walk(folder):
        for fname in files:
            if fname.lower().endswith(".vcxproj"):
                all_vcxproj.append(os.path.join(rootdir, fname))

    if not all_vcxproj:
        actions.append("[SKIP] No .vcxproj to patch with vcpkg paths.")
        return

    for vcxproj_path in all_vcxproj:
        proj_dir = os.path.dirname(vcxproj_path)
        fname    = os.path.basename(vcxproj_path)

        try:
            tree = ET.parse(vcxproj_path)
            root = tree.getroot()
        except ET.ParseError as exc:
            actions.append(f"[SKIP] {fname}: XML parse error — {exc}")
            continue

        ns_m  = re.match(r"^\{(.*?)\}", root.tag)
        xmlns = ns_m.group(1) if ns_m else ""

        def _t(name):
            return f"{{{xmlns}}}{name}" if xmlns else name

        changed = False

        for idg in root.iter(_t("ItemDefinitionGroup")):
            clc = idg.find(_t("ClCompile"))
            if clc is not None:
                aid = clc.find(_t("AdditionalIncludeDirectories"))
                if aid is None:
                    aid = ET.SubElement(clc, _t("AdditionalIncludeDirectories"))
                    aid.text = "%(AdditionalIncludeDirectories)"

                existing_text = (aid.text or "").replace(";%(AdditionalIncludeDirectories)", "").rstrip(";")
                existing_norm = {
                    os.path.normpath(p).lower()
                    for p in existing_text.split(";") if p.strip()
                }
                parts = [p for p in existing_text.split(";") if p.strip()]
                for inc in sorted(new_inc_dirs):
                    if os.path.normpath(inc).lower() not in existing_norm:
                        parts.append(inc)
                        existing_norm.add(os.path.normpath(inc).lower())
                        changed = True
                aid.text = ";".join(parts) + ";%(AdditionalIncludeDirectories)"

            link = idg.find(_t("Link"))
            if link is not None and new_lib_dirs:
                # AdditionalLibraryDirectories
                ald = link.find(_t("AdditionalLibraryDirectories"))
                if ald is None:
                    ald = ET.SubElement(link, _t("AdditionalLibraryDirectories"))
                    ald.text = "%(AdditionalLibraryDirectories)"
                existing_ld = (ald.text or "").replace(";%(AdditionalLibraryDirectories)", "").rstrip(";")
                existing_ld_norm = {
                    os.path.normpath(p).lower()
                    for p in existing_ld.split(";") if p.strip()
                }
                ld_parts = [p for p in existing_ld.split(";") if p.strip()]
                for lib in sorted(new_lib_dirs):
                    if os.path.normpath(lib).lower() not in existing_ld_norm:
                        ld_parts.append(lib)
                        existing_ld_norm.add(os.path.normpath(lib).lower())
                        changed = True
                ald.text = ";".join(ld_parts) + ";%(AdditionalLibraryDirectories)"

                # AdditionalDependencies
                if new_libs:
                    ad = link.find(_t("AdditionalDependencies"))
                    if ad is None:
                        ad = ET.SubElement(link, _t("AdditionalDependencies"))
                        ad.text = "%(AdditionalDependencies)"
                    existing_ad = (ad.text or "").replace(";%(AdditionalDependencies)", "").rstrip(";")
                    existing_ad_set = {p.lower() for p in existing_ad.split(";") if p.strip()}
                    ad_parts = [p for p in existing_ad.split(";") if p.strip()]
                    for lib_file in sorted(new_libs):
                        if lib_file.lower() not in existing_ad_set:
                            ad_parts.append(lib_file)
                            existing_ad_set.add(lib_file.lower())
                            changed = True
                    ad.text = ";".join(ad_parts) + ";%(AdditionalDependencies)"

        if changed:
            if xmlns:
                ET.register_namespace("", xmlns)
            tree.write(vcxproj_path, encoding="utf-8", xml_declaration=True)
            actions.append(f"[FIXED-VCPKG] {fname}: injected vcpkg include/lib paths.")
        else:
            actions.append(f"[OK] {fname}: vcpkg paths already present or no ClCompile/Link found.")


def _suppress_warning_in_vcxproj(folder, warning_number, actions):
    """
    Add /wd<number> to DisableSpecificWarnings in every .vcxproj ClCompile block.
    Used to auto-suppress C4101 (unreferenced local variable) which is noise.
    """
    import xml.etree.ElementTree as ET

    all_vcxproj = []
    for rootdir, _, files in os.walk(folder):
        for fname in files:
            if fname.lower().endswith(".vcxproj"):
                all_vcxproj.append(os.path.join(rootdir, fname))

    for vcxproj_path in all_vcxproj:
        fname = os.path.basename(vcxproj_path)
        try:
            tree = ET.parse(vcxproj_path)
            root = tree.getroot()
        except ET.ParseError as exc:
            actions.append(f"[SKIP] {fname}: XML parse error — {exc}")
            continue

        ns_m  = re.match(r"^\{(.*?)\}", root.tag)
        xmlns = ns_m.group(1) if ns_m else ""

        def _t(name):
            return f"{{{xmlns}}}{name}" if xmlns else name

        changed = False
        for idg in root.iter(_t("ItemDefinitionGroup")):
            clc = idg.find(_t("ClCompile"))
            if clc is None:
                continue
            dsw = clc.find(_t("DisableSpecificWarnings"))
            if dsw is None:
                dsw = ET.SubElement(clc, _t("DisableSpecificWarnings"))
                dsw.text = "%(DisableSpecificWarnings)"
            existing = (dsw.text or "").replace(";%(DisableSpecificWarnings)", "").rstrip(";")
            existing_set = {w.strip() for w in existing.split(";") if w.strip()}
            if str(warning_number) not in existing_set:
                parts = [w for w in existing.split(";") if w.strip()]
                parts.append(str(warning_number))
                dsw.text = ";".join(parts) + ";%(DisableSpecificWarnings)"
                changed = True

        if changed:
            if xmlns:
                ET.register_namespace("", xmlns)
            tree.write(vcxproj_path, encoding="utf-8", xml_declaration=True)
            actions.append(f"[FIXED-WARN] {fname}: suppressed C{warning_number} (DisableSpecificWarnings).")


def fix_vcpkg_deps(folder, build_output=""):
    """
    Auto-Fix Pass 12 — ZSTD, OpenSSL, and other C3861/C1083 external deps.

    Strategy:
      1. Detect which packages are needed from build_output.
      2. Locate or bootstrap-install vcpkg.
      3. Run `vcpkg install <pkg>` for each missing package.
      4. Inject the vcpkg include/ and lib/ paths into every .vcxproj.
      5. Suppress C4101 (unreferenced local 'e') automatically.

    Returns a list of action strings.
    """
    actions = []

    # ── 0. Suppress C4101 (unreferenced local variable 'e') ──────────────────
    if re.search(r"warning C4101", build_output, re.IGNORECASE):
        actions.append("[*] C4101 detected — suppressing via DisableSpecificWarnings...")
        _suppress_warning_in_vcxproj(folder, 4101, actions)

    # ── 1. Determine which vcpkg packages are needed ──────────────────────────
    packages_needed = []
    seen_pkgs = set()
    for pattern, pkg, label in _VCPKG_PACKAGE_MAP:
        if re.search(pattern, build_output, re.IGNORECASE):
            if pkg not in seen_pkgs:
                packages_needed.append((pkg, label))
                seen_pkgs.add(pkg)

    if not packages_needed:
        if not re.search(r"warning C4101", build_output, re.IGNORECASE):
            actions.append("[INFO] No ZSTD/OpenSSL/vcpkg errors detected.")
        return actions

    actions.append(f"[*] Packages needed: {', '.join(label for _, label in packages_needed)}")

    # ── 2. Locate vcpkg ───────────────────────────────────────────────────────
    vcpkg_exe = _find_vcpkg()

    if not vcpkg_exe:
        # Bootstrap vcpkg into C:\vcpkg
        vcpkg_dir = r"C:\vcpkg"
        actions.append(f"[*] vcpkg not found — attempting to clone and bootstrap into {vcpkg_dir}...")
        try:
            if not os.path.isdir(vcpkg_dir):
                rc, out = subprocess.run(
                    ["git", "clone", "https://github.com/microsoft/vcpkg.git", vcpkg_dir],
                    capture_output=True, text=True, timeout=300
                ), ""
                if hasattr(rc, "returncode"):
                    out = (rc.stdout or "") + (rc.stderr or "")
                    rc  = rc.returncode
                else:
                    rc = 0
                if rc != 0:
                    actions.append(f"[ERROR] git clone vcpkg failed — install vcpkg manually: https://vcpkg.io/en/getting-started")
                    actions.append("[MANUAL] Run: git clone https://github.com/microsoft/vcpkg C:\\vcpkg && C:\\vcpkg\\bootstrap-vcpkg.bat")
                    return actions
                actions.append("[+] vcpkg cloned.")

            bootstrap = os.path.join(vcpkg_dir, "bootstrap-vcpkg.bat")
            if os.path.isfile(bootstrap):
                proc = subprocess.run(
                    [bootstrap, "-disableMetrics"],
                    capture_output=True, text=True, timeout=120
                )
                if proc.returncode == 0:
                    actions.append("[+] vcpkg bootstrapped successfully.")
                else:
                    actions.append(f"[WARN] bootstrap-vcpkg.bat exited {proc.returncode} — may still work.")

            vcpkg_exe = os.path.join(vcpkg_dir, "vcpkg.exe")
            if not os.path.isfile(vcpkg_exe):
                actions.append("[ERROR] vcpkg.exe not found after bootstrap. Install manually.")
                actions.append("[MANUAL] https://vcpkg.io/en/getting-started")
                return actions
        except Exception as exc:
            actions.append(f"[ERROR] vcpkg bootstrap failed: {exc}")
            actions.append("[MANUAL] Install vcpkg: https://vcpkg.io/en/getting-started")
            actions.append("[MANUAL] Then run: vcpkg install zstd:x64-windows-static openssl:x64-windows-static")
            return actions

    actions.append(f"[INFO] Using vcpkg at: {vcpkg_exe}")

    # ── 3. Install each package ───────────────────────────────────────────────
    for pkg, label in packages_needed:
        actions.append(f"[*] Installing {label} via vcpkg ({pkg})...")
        try:
            proc = subprocess.run(
                [vcpkg_exe, "install", pkg, "--recurse"],
                capture_output=True, text=True,
                encoding="utf-8", errors="ignore",
                timeout=600
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            for line in output.splitlines():
                line = line.strip()
                if not line:
                    continue
                lo = line.lower()
                if "error" in lo or "fail" in lo:
                    actions.append(f"  [ERR] {line}")
                elif "already installed" in lo or "up-to-date" in lo:
                    actions.append(f"  [OK] {label} already installed.")
                elif "installing" in lo or "succeed" in lo or "package" in lo:
                    actions.append(f"  [+] {line}")

            if proc.returncode == 0:
                actions.append(f"[FIXED-VCPKG] {label} installed successfully.")
            else:
                actions.append(f"[WARN] vcpkg install {pkg} exited {proc.returncode} — check output above.")
        except subprocess.TimeoutExpired:
            actions.append(f"[ERROR] vcpkg install {label} timed out (>10 min) — run manually:")
            actions.append(f"[MANUAL] {vcpkg_exe} install {pkg}")
        except Exception as exc:
            actions.append(f"[ERROR] vcpkg install {label} failed: {exc}")

    # ── 4. Inject vcpkg include/lib paths into .vcxproj ──────────────────────
    installed_root = _vcpkg_installed_root(vcpkg_exe)
    if installed_root:
        actions.append(f"[*] Injecting vcpkg paths from: {installed_root}")
        _inject_vcpkg_paths(folder, installed_root, packages_needed, actions)
    else:
        actions.append("[WARN] Could not locate vcpkg installed/ root — add include/lib paths manually.")
        vcpkg_dir = os.path.dirname(vcpkg_exe)
        actions.append(f"[MANUAL] Add to AdditionalIncludeDirectories: {vcpkg_dir}\\installed\\x64-windows-static\\include")
        actions.append(f"[MANUAL] Add to AdditionalLibraryDirectories: {vcpkg_dir}\\installed\\x64-windows-static\\lib")

    return actions


# ─────────────────────────────────────────────
# Auto-Fix Loop Orchestrator
# ─────────────────────────────────────────────

# Maps error patterns → which fix functions to call automatically
_AUTO_FIX_RULES = [
    # (regex_pattern,  fix_function,   needs_build_output, label)
    # C4101 + ZSTD + OpenSSL: run BEFORE generic C1083/LNK passes
    (r"warning C4101|ZSTD_decompress|error C3861.*zstd"
     r"|error C1083[^']*(?:zstd|openssl)|OPENSSL_|openssl/",
                                       "fix_vcpkg_deps",    True,  "FIX DEPS (vcpkg)"),
    (r"error C1083",                   "fix_cpp_includes",  True,  "FIX INCS"),
    (r"error C1010",                   "fix_pch",           True,  "FIX PCH"),
    (r"LNK2001|LNK2019|LNK1181|LNK1104|LNK1120", "fix_missing_libs", True, "FIX LIBS"),
    (r"error CS2001|error CS0246",     "fix_cs_files",      False, "FIX CS FILES"),
    (r"error CS0006|NU1101|NU1102|NU1103|packages\.config.*not found",
                                       "fix_dotnet_restore",False, "NUGET RESTORE"),
    (r"C4003.*LUAU_FASTFLAGVARIABLE|C2051.*BytecodeUtils|LuauBytecodeType.*undeclared"
     r"|error C1083[^']*'[^']*(?:Luau/|luau/|lua\.h|luaconf\.h|lualib\.h)",
                                       "fix_luau",          True,  "FIX LUAU"),
]

_FIX_FUNC_MAP = {
    "fix_vcpkg_deps":    lambda folder, out: fix_vcpkg_deps(folder, out),
    "fix_cpp_includes":  lambda folder, out: fix_cpp_missing_includes(folder, out),
    "fix_pch":           lambda folder, out: fix_pch(folder, out),
    "fix_missing_libs":  lambda folder, out: fix_missing_libs(folder, out),
    "fix_cs_files":      lambda folder, out: fix_missing_cs_files(folder),
    "fix_dotnet_restore":lambda folder, out: _run_dotnet_restore_cmd(folder).get("output", "").splitlines(),
    "fix_luau":          lambda folder, out: fix_luau_submodule(folder, last_build_output=out),
}


def auto_fix_from_output(folder, build_output, emit_line=None):
    """
    Given build output, automatically determines which fix passes apply
    and runs them. Returns (actions_list, fix_labels_applied).
    """
    actions_all   = []
    labels_applied = []

    def log(text, cls="dim"):
        actions_all.append(text)
        if emit_line:
            emit_line(text, cls)

    for pattern, func_name, needs_output, label in _AUTO_FIX_RULES:
        if re.search(pattern, build_output, re.IGNORECASE):
            log(f"[AUTO-FIX] Detected pattern → running {label}...", "warn")
            try:
                out_arg = build_output if needs_output else ""
                fix_results = _FIX_FUNC_MAP[func_name](folder, out_arg)
                for line in fix_results:
                    cls = "info"  if line.startswith("[FIXED") else \
                          "error" if line.startswith("[ERROR") else \
                          "warn"  if line.startswith("[WARN")  else "dim"
                    log(line, cls)
                labels_applied.append(label)
            except Exception as exc:
                log(f"[ERROR] {label} threw: {exc}", "error")

    if not labels_applied:
        log("[AUTO-FIX] No auto-applicable fixes matched this build output.", "dim")

    return actions_all, labels_applied


# ─────────────────────────────────────────────
# Build Output SHA256 Hash
# ─────────────────────────────────────────────

def sha256_file(path):
    """Returns hex SHA256 of a file, or None on error."""
    import hashlib
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


# ─────────────────────────────────────────────
# Diagnosis
# ─────────────────────────────────────────────

def diagnose(text):
    fixes = []
    for pattern, msg in PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            if msg not in fixes:
                fixes.append(msg)
    if not fixes:
        fixes.append("No known issue detected — inspect compiler output above manually.")
    return fixes


# ─────────────────────────────────────────────
# JavaScript API (called from the UI)
# ─────────────────────────────────────────────

class Api:
    def __init__(self):
        self._window      = None
        self._last_output = ""   # stored after every build for C1083 auto-fix

    def set_window(self, window):
        self._window = window

    # ── scan ─────────────────────────────────────────────────────────────────
    def scan(self, folder):
        if not os.path.isdir(folder):
            return json.dumps({"ok": False, "error": "Folder not found"})
        try:
            scan    = scan_project(folder)
            vcvars  = find_vcvars64()
            fw_info = check_framework_consistency(folder)

            if scan["sln"]:
                strategy = "MSBuild (solution)"
            elif scan["vcxproj"]:
                strategy = "MSBuild (vcxproj)"
            elif scan["csproj"]:
                strategy = "MSBuild (C# csproj)"
            elif scan["cpp"] or scan["c"]:
                strategy = "MSVC cl.exe (source files)"
            elif scan["cmake"]:
                strategy = "CMake"
            else:
                strategy = "Unknown — no buildable files found"

            return json.dumps({
                "ok": True,
                "files": scan,
                "strategy": strategy,
                "vcvars": vcvars or "NOT FOUND",
                "frameworks": fw_info
            })
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    # ── fix_cs_files (passes 1 & 2 only) ────────────────────────────────────
    def fix_cs_files(self, folder):
        if not os.path.isdir(folder):
            return json.dumps({"ok": False, "error": "Folder not found"})
        try:
            actions = fix_missing_cs_files(folder)
            return json.dumps({"ok": True, "actions": actions})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    # ── fix_duplicate_refs (pass 3) ──────────────────────────────────────────
    def fix_duplicate_refs(self, folder):
        if not os.path.isdir(folder):
            return json.dumps({"ok": False, "error": "Folder not found"})
        try:
            actions = fix_duplicate_refs(folder)
            return json.dumps({"ok": True, "actions": actions})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    # ── fix_cpp_headers (pass 5) ─────────────────────────────────────────────
    def fix_cpp_headers(self, folder):
        if not os.path.isdir(folder):
            return json.dumps({"ok": False, "error": "Folder not found"})
        try:
            actions = fix_cpp_headers(folder)
            return json.dumps({"ok": True, "actions": actions})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    # ── fix_vcxproj_items (pass 9 — missing ClCompile / ClInclude) ──────────
    def fix_vcxproj_items(self, folder):
        if not os.path.isdir(folder):
            return json.dumps({"ok": False, "error": "Folder not found"})
        try:
            actions = fix_vcxproj_missing_items(folder)
            return json.dumps({"ok": True, "actions": actions})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    # ── fix_cpp_includes (pass 6 — C1083 missing include dirs) ──────────────
    def fix_cpp_includes(self, folder, build_output=""):
        if not os.path.isdir(folder):
            return json.dumps({"ok": False, "error": "Folder not found"})
        try:
            output  = build_output if build_output else self._last_output
            actions = fix_cpp_missing_includes(folder, output)
            return json.dumps({"ok": True, "actions": actions})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    # ── fix_luau (pass 7 — Luau submodule + include injection) ───────────────
    def fix_luau(self, folder):
        if not os.path.isdir(folder):
            return json.dumps({"ok": False, "error": "Folder not found"})
        try:
            actions = fix_luau_submodule(
                folder,
                last_build_output=self._last_output,
                emit_line=lambda text, cls: self._emit("log", {"text": text, "cls": cls}),
            )
            return json.dumps({"ok": True, "actions": actions})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    # ── fix_missing_libs (pass 10 — LNK lib finder) ─────────────────────────
    def fix_missing_libs(self, folder):
        if not os.path.isdir(folder):
            return json.dumps({"ok": False, "error": "Folder not found"})
        try:
            actions = fix_missing_libs(folder, self._last_output)
            return json.dumps({"ok": True, "actions": actions})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    # ── fix_pch (pass 11 — PCH auto-stub / disable) ──────────────────────────
    def fix_pch(self, folder):
        if not os.path.isdir(folder):
            return json.dumps({"ok": False, "error": "Folder not found"})
        try:
            actions = fix_pch(folder, self._last_output)
            return json.dumps({"ok": True, "actions": actions})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    # ── fix_vcpkg_deps (pass 12 — ZSTD / OpenSSL / C4101) ───────────────────
    def fix_vcpkg_deps(self, folder):
        if not os.path.isdir(folder):
            return json.dumps({"ok": False, "error": "Folder not found"})
        try:
            actions = fix_vcpkg_deps(folder, self._last_output)
            return json.dumps({"ok": True, "actions": actions})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    # ── auto_fix_loop ─────────────────────────────────────────────────────────
    def auto_fix_loop(self, folder, config_json, max_retries=3):
        """
        Run build → if failed, auto-detect and apply fixes → retry.
        Streams live via JS events. max_retries caps the loop.
        """
        def _run():
            config = json.loads(config_json)

            for attempt in range(1, max_retries + 2):
                self._emit("log", {"text": f"", "cls": "dim"})
                self._emit("log", {
                    "text": f"═══ AUTO-FIX LOOP — Attempt {attempt}/{max_retries + 1} ═══",
                    "cls": "heading"
                })

                # ── Build ──────────────────────────────────────────────────────
                try:
                    bat_path, _ = generate_build_bat(folder, config)
                    self._emit("log", {"text": "[+] build.bat generated.", "cls": "info"})
                except Exception as exc:
                    self._emit("log", {"text": f"[!] {exc}", "cls": "error"})
                    self._emit("autofix_done", {"success": False, "attempts": attempt,
                                                "diag": [str(exc)], "exePath": ""})
                    return

                captured = ""
                proc = subprocess.Popen(
                    ["cmd.exe", "/c", bat_path],
                    cwd=folder,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                )
                for line in proc.stdout:
                    stripped  = line.rstrip()
                    captured += stripped + "\n"
                    lo = stripped.lower()
                    if any(x in lo for x in ["error", "fatal", "failed"]):
                        cls = "error"
                    elif "warning" in lo:
                        cls = "warn"
                    elif any(x in lo for x in ["succeeded", "->", "build complete"]):
                        cls = "info"
                    else:
                        cls = "default"
                    self._emit("log", {"text": stripped, "cls": cls})

                proc.wait()
                self._last_output = captured
                success = proc.returncode == 0

                if success:
                    # ── Find EXE ──────────────────────────────────────────────
                    exe_path = ""
                    conf      = config.get("config", "Release")
                    proj_name = os.path.basename(folder.rstrip("\\/")) or "project"
                    candidates = [
                        os.path.join(folder, "x64", conf, f"{proj_name}.exe"),
                        os.path.join(folder, conf, f"{proj_name}.exe"),
                        os.path.join(folder, f"{proj_name}.exe"),
                    ]
                    arrow_m = re.search(r"->\s+(.+\.exe)", captured, re.IGNORECASE)
                    if arrow_m:
                        ap = arrow_m.group(1).strip()
                        if not os.path.isabs(ap):
                            ap = os.path.join(folder, ap)
                        candidates.insert(0, ap)
                    for c in candidates:
                        if os.path.isfile(c):
                            exe_path = c
                            break

                    # ── SHA256 fingerprint ────────────────────────────────────
                    if exe_path:
                        h = sha256_file(exe_path)
                        if h:
                            self._emit("log", {
                                "text": f"[SHA256] {os.path.basename(exe_path)} → {h}",
                                "cls": "dim"
                            })

                    self._emit("autofix_done", {
                        "success":  True,
                        "attempts": attempt,
                        "diag":     diagnose(captured),
                        "exePath":  exe_path,
                        "sha256":   sha256_file(exe_path) if exe_path else "",
                    })
                    return

                # ── Build failed — attempt auto-fix before retrying ────────────
                if attempt <= max_retries:
                    self._emit("log", {"text": "", "cls": "dim"})
                    self._emit("log", {
                        "text": f"[AUTO-FIX] Build failed — analyzing errors and applying fixes...",
                        "cls": "warn"
                    })
                    fix_actions, labels = auto_fix_from_output(
                        folder, captured,
                        emit_line=lambda t, c: self._emit("log", {"text": t, "cls": c})
                    )
                    if not labels:
                        self._emit("log", {
                            "text": "[AUTO-FIX] No applicable auto-fix found — stopping loop.",
                            "cls": "error"
                        })
                        break
                    self._emit("log", {
                        "text": f"[AUTO-FIX] Applied: {', '.join(labels)} — retrying build...",
                        "cls": "info"
                    })
                else:
                    break

            # Loop exhausted without success
            self._emit("autofix_done", {
                "success":  False,
                "attempts": min(attempt, max_retries + 1),
                "diag":     diagnose(self._last_output),
                "exePath":  "",
                "sha256":   "",
            })

        threading.Thread(target=_run, daemon=True).start()
        return json.dumps({"ok": True})


    def run_dotnet_restore(self, folder):
        if not os.path.isdir(folder):
            return json.dumps({"ok": False, "output": "Folder not found", "returncode": -1})
        try:
            r = _run_dotnet_restore_cmd(folder)
            return json.dumps({"ok": r["ok"], "output": r["output"], "returncode": r["returncode"]})
        except Exception as exc:
            return json.dumps({"ok": False, "output": str(exc), "returncode": -1})

    # ── fix_all (all passes) ─────────────────────────────────────────────────
    def fix_all(self, folder):
        if not os.path.isdir(folder):
            return json.dumps({"ok": False, "error": "Folder not found"})
        try:
            actions = fix_all_cs(folder)
            return json.dumps({"ok": True, "actions": actions})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    # ── generate ─────────────────────────────────────────────────────────────
    def generate(self, folder, config_json):
        config = json.loads(config_json)
        if not os.path.isdir(folder):
            return json.dumps({"ok": False, "error": "Folder not found"})
        try:
            bat_path, _ = generate_build_bat(folder, config)
            with open(bat_path, encoding="utf-8") as f:
                preview = f.read()
            return json.dumps({"ok": True, "path": bat_path, "preview": preview})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    # ── run_build ─────────────────────────────────────────────────────────────
    def run_build(self, folder, config_json):
        def _run():
            config = json.loads(config_json)

            # Always regenerate build.bat (creates .vcxproj if needed)
            try:
                bat_path, scan_result = generate_build_bat(folder, config)
                self._emit("log", {"text": "[+] build.bat generated.", "cls": "info"})
            except Exception as exc:
                self._emit("log", {"text": f"[!] {exc}", "cls": "error"})
                self._emit("done", {"success": False, "diag": [str(exc)], "exePath": ""})
                return

            captured = ""

            proc = subprocess.Popen(
                ["cmd.exe", "/c", bat_path],
                cwd=folder,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )

            for line in proc.stdout:
                stripped  = line.rstrip()
                captured += stripped + "\n"

                lo = stripped.lower()
                if any(x in lo for x in ["error", "fatal", "failed"]):
                    cls = "error"
                elif any(x in lo for x in ["warning"]):
                    cls = "warn"
                elif any(x in lo for x in ["succeeded", "->", "build complete"]):
                    cls = "info"
                else:
                    cls = "default"

                self._emit("log", {"text": stripped, "cls": cls})

            proc.wait()
            success = proc.returncode == 0
            fixes   = diagnose(captured)
            self._last_output = captured   # ← store for C1083 fix pass

            # ── Locate the produced .exe ──────────────────────────────────
            exe_path = ""
            if success:
                conf      = config.get("config", "Release")
                proj_name = os.path.basename(folder.rstrip("\\/")) or "project"
                candidates = [
                    os.path.join(folder, "x64", conf, f"{proj_name}.exe"),
                    os.path.join(folder, conf, f"{proj_name}.exe"),
                    os.path.join(folder, "x64", conf, "app.exe"),
                    os.path.join(folder, conf, "app.exe"),
                    os.path.join(folder, f"{proj_name}.exe"),
                    os.path.join(folder, "app.exe"),
                ]
                # Parse MSBuild "-> path\app.exe" lines for the exact output path
                arrow_m = re.search(r"->\s+(.+\.exe)", captured, re.IGNORECASE)
                if arrow_m:
                    arrow_path = arrow_m.group(1).strip()
                    abs_arrow  = arrow_path if os.path.isabs(arrow_path) else os.path.join(folder, arrow_path)
                    candidates.insert(0, abs_arrow)

                for c in candidates:
                    if os.path.isfile(c):
                        exe_path = c
                        break

                if exe_path:
                    self._emit("log", {"text": f"[OK] Output EXE: {exe_path}", "cls": "info"})
                    h = sha256_file(exe_path)
                    if h:
                        self._emit("log", {
                            "text": f"[SHA256] {os.path.basename(exe_path)} → {h}",
                            "cls": "dim"
                        })
                else:
                    self._emit("log", {"text": "[?] Build succeeded but .exe location unknown.", "cls": "warn"})

            _has_luau = bool(re.search(
                r"error C1083[^']*'[^']*(?:Luau/|luau/|xxhash\.h"
                r"|lua\.h|luaconf\.h|lualib\.h|luacode\.h|luacodegen\.h"
                r"|lapi\.h|ldo\.h|lgc\.h|lobject\.h|lstate\.h|lstring\.h"
                r"|ltable\.h|lmem\.h|ldebug\.h|lfunc\.h|lvm\.h"
                r"|compiler\.cpp|lexer\.cpp|parser\.cpp|location\.cpp"
                r"|confusables\.cpp|constantfolding\.cpp|costmodel\.cpp"
                r"|lcode\.cpp|stringutils\.cpp|tableshape\.cpp|timetrace\.cpp"
                r"|types\.cpp|valuetracking\.cpp|bytecodebuilder\.cpp|bytecodeutils\.cpp"
                r"|lapi\.cpp|ldo\.cpp|lgc\.cpp|lobject\.cpp|lstate\.cpp"
                r"|lstring\.cpp|lvmexecute\.cpp|lvmload\.cpp|lvmutils\.cpp)",
                captured, re.IGNORECASE
            )) or bool(re.search(
                # Version-mismatch errors — header/source from different Luau commits
                r"C4003.*LUAU_FASTFLAGVARIABLE"          # macro arg count changed
                r"|C2051.*BytecodeUtils"                  # enum not constant (tag type changed)
                r"|C2065.*hasConstants"                   # member renamed between versions
                r"|C2327.*BytecodeBuilder.*constants"     # member not a type (renamed)
                r"|LuauBytecodeType.*undeclared"          # type moved/renamed
                r"|C2039.*Type_Vector.*BytecodeBuilder",  # struct member gone in newer ver
                captured, re.IGNORECASE
            ))
            self._emit("done", {
                "success":    success,
                "returncode": proc.returncode,
                "diag":       fixes,
                "exePath":    exe_path,
                "sha256":     sha256_file(exe_path) if exe_path else "",
                "hasC1083":   bool(re.search(r"error C1083", captured, re.IGNORECASE)),
                "hasLuau":    _has_luau,
                "hasLnk":     bool(re.search(r"LNK2001|LNK2019|LNK1181|LNK1104|LNK1120", captured, re.IGNORECASE)),
                "hasPch":     bool(re.search(r"error C1010", captured, re.IGNORECASE)),
            })

        threading.Thread(target=_run, daemon=True).start()
        return json.dumps({"ok": True})

    def _emit(self, event, data):
        payload = json.dumps(data).replace("\\", "\\\\").replace("'", "\\'")
        if self._window:
            self._window.evaluate_js(f"window.__bdEvent('{event}', JSON.parse('{payload}'))")

    def browse(self):
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if result and len(result) > 0:
            return json.dumps({"ok": True, "path": result[0]})
        return json.dumps({"ok": False})


# ─────────────────────────────────────────────
# HTML / CSS / JS UI
# ─────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Build Doctor</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0e0f11;--surface:#16181c;--surface2:#1e2126;
  --border:#2a2d34;--border2:#383c45;
  --accent:#00d084;--accent2:#00a868;
  --warn:#f5a623;--error:#ff4e4e;--purple:#a78bfa;
  --text:#e2e4e8;--muted:#6b7280;
  --mono:'IBM Plex Mono',monospace;
  --sans:'IBM Plex Sans',sans-serif;
}
body{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:14px;
     height:100vh;display:flex;flex-direction:column;overflow:hidden;user-select:none}

/* ── Titlebar ── */
.titlebar{
  background:var(--surface);border-bottom:1px solid var(--border);
  padding:0 16px;height:42px;display:flex;align-items:center;gap:12px;
  flex-shrink:0;-webkit-app-region:drag;
}
.tb-dot{width:11px;height:11px;border-radius:50%}
.dot-r{background:#ff5f57}.dot-y{background:#febc2e}.dot-g{background:#28c840}
.tb-name{font-family:var(--mono);font-size:13px;font-weight:600;margin-left:8px;letter-spacing:.04em}
.tb-badge{font-family:var(--mono);font-size:10px;background:var(--accent2);color:#000;
          padding:2px 7px;border-radius:3px;font-weight:600;letter-spacing:.06em}
.tb-version{font-family:var(--mono);font-size:10px;color:var(--muted);margin-left:auto}

/* ── Layout ── */
.main{display:flex;flex:1;overflow:hidden}

/* ── Sidebar ── */
.sidebar{
  width:220px;background:var(--surface);border-right:1px solid var(--border);
  display:flex;flex-direction:column;flex-shrink:0;overflow-y:auto;
}
.sb-section{padding:10px 0;border-bottom:1px solid var(--border)}
.sb-label{font-family:var(--mono);font-size:10px;color:var(--muted);
          letter-spacing:.1em;padding:0 14px 7px;font-weight:600}
.sb-item{
  display:flex;align-items:center;gap:9px;padding:7px 14px;
  cursor:pointer;font-size:13px;color:var(--muted);
  transition:background .1s,color .1s;border-left:2px solid transparent;
}
.sb-item:hover{background:var(--surface2);color:var(--text)}
.sb-item.active{background:var(--surface2);color:var(--accent);border-left-color:var(--accent)}
.sb-item svg{flex-shrink:0;opacity:.7}
.sb-item.active svg,.sb-item:hover svg{opacity:1}
.sb-item.sb-fix{color:var(--warn)}
.sb-item.sb-fix:hover{color:var(--warn)}
.sb-item.sb-fix-all{color:var(--purple)}
.sb-item.sb-fix-all:hover{color:var(--purple)}
.status-area{margin-top:auto;padding:12px 14px;border-top:1px solid var(--border)}
.sdot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;vertical-align:middle}
.sdot.idle{background:var(--muted)}
.sdot.building{background:var(--warn);animation:pulse 1s ease-in-out infinite}
.sdot.ok{background:var(--accent)}
.sdot.err{background:var(--error)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.stext{font-family:var(--mono);font-size:11px;color:var(--muted)}

/* ── Content ── */
.content{flex:1;display:flex;flex-direction:column;overflow:hidden}

/* ── Toolbar ── */
.toolbar{
  background:var(--surface);border-bottom:1px solid var(--border);
  padding:9px 14px;display:flex;align-items:center;gap:9px;flex-shrink:0;
  flex-wrap:wrap;
}
.path-label{font-family:var(--mono);font-size:11px;color:var(--muted);
            white-space:nowrap;letter-spacing:.05em}
.path-input{
  flex:1;background:var(--surface2);border:1px solid var(--border);
  border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px;
  padding:5px 10px;outline:none;transition:border-color .15s;min-width:0;
}
.path-input:focus{border-color:var(--accent)}
.path-input::placeholder{color:var(--muted)}
.btn{
  font-family:var(--mono);font-size:12px;font-weight:600;
  padding:5px 13px;border-radius:4px;border:1px solid var(--border2);
  cursor:pointer;transition:all .12s;white-space:nowrap;
  letter-spacing:.03em;background:transparent;color:var(--muted);
}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn:active{transform:scale(.97)}
.btn-primary{background:var(--accent);color:#000;border-color:var(--accent)}
.btn-primary:hover{background:var(--accent2);border-color:var(--accent2);color:#000}
.btn-primary:disabled{background:var(--surface2);color:var(--muted);
                       border-color:var(--border);cursor:not-allowed;transform:none}
.btn-warn{border-color:var(--warn);color:var(--warn)}
.btn-warn:hover{background:rgba(245,166,35,.1);color:var(--warn);border-color:var(--warn)}
.btn-purple{border-color:var(--purple);color:var(--purple)}
.btn-purple:hover{background:rgba(167,139,250,.1);color:var(--purple);border-color:var(--purple)}

/* ── Panels ── */
.panels{display:flex;flex:1;overflow:hidden}
.panel-left{flex:0 0 270px;border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
.panel-right{flex:1;display:flex;flex-direction:column;overflow:hidden}
.panel-header{
  background:var(--surface);border-bottom:1px solid var(--border);padding:7px 14px;
  font-family:var(--mono);font-size:10px;color:var(--muted);letter-spacing:.1em;font-weight:600;
  display:flex;align-items:center;justify-content:space-between;flex-shrink:0;
}
.ph-badge{
  background:var(--surface2);border:1px solid var(--border);border-radius:10px;
  font-size:10px;padding:1px 7px;font-family:var(--mono);
}

/* ── File Tree ── */
.file-tree{overflow-y:auto;flex:1;padding:6px 0}
.tree-group{margin-bottom:2px}
.tree-ext{
  font-family:var(--mono);font-size:10px;padding:3px 14px;letter-spacing:.08em;
  display:flex;align-items:center;gap:6px;font-weight:600;
}
.tree-file{
  font-family:var(--mono);font-size:11px;color:var(--muted);
  padding:2px 14px 2px 22px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  cursor:default;line-height:1.6;
}
.tree-file:hover{color:var(--text);background:var(--surface2)}
.tree-strategy{
  margin:8px 10px;padding:7px 10px;background:var(--surface2);border:1px solid var(--border);
  border-radius:4px;font-family:var(--mono);font-size:10px;color:var(--muted);line-height:1.7;
}
.tree-strategy span{color:var(--accent)}
.tree-warn{
  margin:4px 10px;padding:6px 10px;background:rgba(245,166,35,.08);
  border:1px solid rgba(245,166,35,.3);border-radius:4px;
  font-family:var(--mono);font-size:10px;color:var(--warn);line-height:1.6;
}
.empty-msg{padding:18px 14px;font-family:var(--mono);font-size:11px;color:var(--muted);line-height:1.8}

/* ── Terminal ── */
.terminal{
  flex:1;overflow-y:auto;padding:12px 16px;
  font-family:var(--mono);font-size:12px;line-height:1.65;background:var(--bg);
}
.l-default{color:#9ba3af}.l-info{color:var(--accent)}.l-warn{color:var(--warn)}
.l-error{color:var(--error)}.l-dim{color:var(--muted)}.l-heading{color:var(--text);font-weight:600}
.l-purple{color:var(--purple)}
.cursor{display:inline-block;width:8px;height:13px;background:var(--accent);
        vertical-align:middle;animation:blink 1.1s step-end infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0}}

/* ── Diagnosis Panel ── */
.diag-panel{border-top:1px solid var(--border);background:var(--surface);flex-shrink:0}
.diag-hdr{
  padding:7px 14px;font-family:var(--mono);font-size:10px;color:var(--muted);
  letter-spacing:.1em;font-weight:600;display:flex;align-items:center;
  gap:8px;cursor:pointer;border-bottom:1px solid transparent;
}
.diag-hdr.open{border-bottom-color:var(--border)}
.diag-body{max-height:150px;overflow-y:auto;padding:8px 12px}
.diag-item{
  display:flex;align-items:flex-start;gap:10px;
  padding:5px 0;border-bottom:1px solid var(--border);font-size:12px;
}
.diag-item:last-child{border-bottom:none}
.diag-badge{
  font-size:10px;font-family:var(--mono);padding:1px 5px;
  border-radius:3px;flex-shrink:0;margin-top:2px;font-weight:600;
}
.badge-fix{background:#1a2e1e;color:var(--accent);border:1px solid var(--accent2)}
.badge-ok{background:#1a2415;color:#4ade80;border:1px solid #166534}
.badge-err{background:#2e1a1a;color:var(--error);border:1px solid #7f1d1d}
.diag-text{font-family:var(--sans);color:var(--text);line-height:1.5}

/* ── Config Panel ── */
.config-panel{padding:18px;overflow-y:auto;display:none;flex:1;flex-direction:column;gap:14px}
.config-panel.show{display:flex}
.cfg-group{
  background:var(--surface2);border:1px solid var(--border);border-radius:6px;padding:14px;
}
.cfg-title{font-family:var(--mono);font-size:10px;color:var(--muted);
           letter-spacing:.08em;margin-bottom:10px;font-weight:600}
.cfg-row{
  display:flex;align-items:center;justify-content:space-between;
  padding:6px 0;border-bottom:1px solid var(--border);gap:12px;
}
.cfg-row:last-child{border-bottom:none}
.cfg-row-label{font-size:13px;color:var(--text)}
.cfg-sel{
  background:var(--surface);border:1px solid var(--border);color:var(--text);
  font-family:var(--mono);font-size:12px;padding:4px 8px;border-radius:4px;outline:none;
}
.cfg-sel:focus{border-color:var(--accent)}
.cfg-tog{
  width:34px;height:18px;background:var(--border2);border-radius:9px;
  cursor:pointer;position:relative;flex-shrink:0;transition:background .2s;
}
.cfg-tog.on{background:var(--accent2)}
.cfg-tog::after{
  content:'';position:absolute;top:3px;left:3px;width:12px;height:12px;
  border-radius:50%;background:#fff;transition:transform .2s;
}
.cfg-tog.on::after{transform:translateX(16px)}

/* ── Scrollbars ── */
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border2);border-radius:3px}
::-webkit-scrollbar-thumb:hover{background:#4a5060}
</style>
</head>
<body>

<div class="titlebar">
  <div class="tb-dot dot-r"></div>
  <div class="tb-dot dot-y"></div>
  <div class="tb-dot dot-g"></div>
  <span class="tb-name">BUILD DOCTOR</span>
  <span class="tb-badge">MSVC</span>
  <span class="tb-version">v9.0</span>
</div>

<div class="main">
  <!-- Sidebar -->
  <div class="sidebar">
    <div class="sb-section">
      <div class="sb-label">WORKSPACE</div>
      <div class="sb-item active" id="nav-build" onclick="showTab('build')">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
        </svg>
        Build
      </div>
      <div class="sb-item" id="nav-config" onclick="showTab('config')">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="3"/>
          <path d="M19.07 4.93a10 10 0 0 0-14.14 0M4.93 19.07a10 10 0 0 0 14.14 0"/>
          <path d="M12 2v2M12 20v2M2 12h2M20 12h2"/>
        </svg>
        Settings
      </div>
    </div>

    <div class="sb-section">
      <div class="sb-label">QUICK ACTIONS</div>
      <div class="sb-item" onclick="doScan()">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        Scan Project
      </div>
      <div class="sb-item" onclick="doGenerate()">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
          <line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="12" y2="17"/>
        </svg>
        Gen build.bat
      </div>
      <div class="sb-item" onclick="doClear()">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="3 6 5 6 21 6"/>
          <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
        </svg>
        Clear Output
      </div>
    </div>

    <div class="sb-section">
      <div class="sb-label">C# FIXERS</div>
      <div class="sb-item sb-fix" onclick="doFixCS()" title="Pass 1&2: Remove dead .cs refs + add missing ProjectReferences">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M12 22C6.477 22 2 17.523 2 12S6.477 2 12 2s10 4.477 10 10-4.477 10-10 10z"/>
          <path d="M9 12l2 2 4-4"/>
        </svg>
        Fix CS Files
      </div>
      <div class="sb-item sb-fix" onclick="doFixDupes()" title="Pass 3: Remove duplicate references and empty ItemGroups">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
        </svg>
        Fix Duplicates
      </div>
      <div class="sb-item sb-fix" onclick="doDotnetRestore()" title="Run dotnet restore to fetch NuGet packages">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="1 4 1 10 7 10"/>
          <path d="M3.51 15a9 9 0 1 0 .49-3.5"/>
        </svg>
        NuGet Restore
      </div>
      <div class="sb-item sb-fix-all" onclick="doFixAll()" title="Run ALL C# fix passes at once">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
        </svg>
        Fix All C# (All Passes)
      </div>
    </div>

    <div class="sb-section">
      <div class="sb-label">C++ FIXERS</div>
      <div class="sb-item sb-fix" onclick="doFixHeaders()" title="Add #pragma once to headers missing include guards">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>
        </svg>
        Fix Header Guards
      </div>
      <div class="sb-item sb-fix" onclick="doFixVcxproj()" title="Sync .vcxproj: add missing ClCompile/ClInclude for disk files, remove dead entries">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
          <line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/>
        </svg>
        Sync .vcxproj Items
      </div>
      <div class="sb-item sb-fix" onclick="doFixLibs()" title="Pass 10: Find missing .lib files and inject into vcxproj linker">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
        </svg>
        Fix Missing .libs
      </div>
      <div class="sb-item sb-fix" onclick="doFixPch()" title="Pass 11: Create PCH stub or disable precompiled headers">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/>
          <polyline points="13 2 13 9 20 9"/>
        </svg>
        Fix PCH
      </div>

    <div class="sb-section">
      <div class="sb-label">AUTOMATION</div>
      <div class="sb-item" style="color:var(--accent)" onclick="doAutoFixLoop()" title="Build → auto-detect errors → fix → retry (up to 3x)">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="1 4 1 10 7 10"/>
          <polyline points="23 20 23 14 17 14"/>
          <path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4-4.64 4.36A9 9 0 0 1 3.51 15"/>
        </svg>
        Auto-Fix Loop
      </div>
    </div>
      <span class="sdot idle" id="sdot"></span>
      <span class="stext" id="stext">IDLE</span>
    </div>
  </div>

  <!-- Content -->
  <div class="content">
    <div class="toolbar">
      <span class="path-label">PATH</span>
      <input class="path-input" id="pathInput" type="text" placeholder="C:\MyProject" />
      <button class="btn" onclick="doBrowse()">BROWSE</button>
      <button class="btn" onclick="doScan()">SCAN</button>
      <button class="btn" onclick="doGenerate()">GEN BAT</button>
      <button class="btn btn-warn" onclick="doFixCS()" title="Fix dead .cs refs + missing ProjectRefs">FIX CS</button>
      <button class="btn btn-warn" onclick="doFixDupes()" title="Remove duplicate refs + empty ItemGroups">FIX DUPES</button>
      <button class="btn btn-warn" onclick="doFixHeaders()" title="Add #pragma once to unguarded headers">FIX HDRS</button>
      <button class="btn btn-warn" onclick="doFixIncludes()" title="Fix C1083: find missing headers and inject include paths into .vcxproj">FIX INCS</button>
      <button class="btn btn-warn" onclick="doFixVcxproj()" title="Auto-sync .vcxproj: add missing ClCompile/ClInclude entries for files on disk, remove dead ones">FIX VCXPROJ</button>
      <button class="btn btn-warn" onclick="doFixLuau()" title="Fix Luau C1083: init submodule or clone luau-lang/luau, then patch .vcxproj">FIX LUAU</button>
      <button class="btn btn-warn" onclick="doFixLibs()" title="Pass 10: Find missing .lib files and inject into .vcxproj linker paths">FIX LIBS</button>
      <button class="btn btn-warn" onclick="doFixPch()" title="Pass 11: Create a PCH stub or disable precompiled headers">FIX PCH</button>
      <button class="btn btn-purple" onclick="doFixAll()" title="Run all C# fix passes">FIX ALL</button>
      <button class="btn" style="border-color:var(--accent);color:var(--accent)" onclick="doAutoFixLoop()" title="Build → auto-detect errors → apply fixes → retry (up to 3 times)">&#9654;&#9654; AUTO-FIX</button>
      <button class="btn btn-primary" id="runBtn" onclick="doRunBuild()">&#9654; RUN BUILD</button>
    </div>

    <!-- Build Tab -->
    <div id="tab-build" class="panels">
      <div class="panel-left">
        <div class="panel-header">
          FILE TREE
          <span class="ph-badge" id="fileCount">0 files</span>
        </div>
        <div class="file-tree" id="fileTree">
          <div class="empty-msg">No project scanned.<br>Enter a path and click SCAN.</div>
        </div>
      </div>
      <div class="panel-right">
        <div class="panel-header">
          OUTPUT
          <span id="buildTime" style="font-size:10px;color:var(--muted)"></span>
        </div>
        <div class="terminal" id="terminal">
          <span class="l-dim">// Build Doctor v3.0 — ready</span><br>
          <span class="l-dim">// Enter a project path and run a build</span><br><br>
          <span class="cursor"></span>
        </div>
        <div class="diag-panel">
          <div class="diag-hdr" id="diagHdr" onclick="toggleDiag()">
            DIAGNOSIS
            <span style="color:var(--accent);font-size:11px" id="diagChev">&#9650;</span>
          </div>
          <div id="diagBody" style="display:none" class="diag-body"></div>
        </div>
      </div>
    </div>

    <!-- Config Tab -->
    <div id="tab-config" class="config-panel">
      <div class="cfg-group">
        <div class="cfg-title">COMPILER FLAGS</div>
        <div class="cfg-row">
          <span class="cfg-row-label">C++ Standard</span>
          <select class="cfg-sel" id="cfgStd">
            <option>/std:c++17</option>
            <option>/std:c++20</option>
            <option>/std:c++14</option>
            <option>/std:c++latest</option>
          </select>
        </div>
        <div class="cfg-row">
          <span class="cfg-row-label">Configuration</span>
          <select class="cfg-sel" id="cfgConf">
            <option>Release</option>
            <option>Debug</option>
            <option>RelWithDebInfo</option>
          </select>
        </div>
        <div class="cfg-row">
          <span class="cfg-row-label">Runtime Linking</span>
          <select class="cfg-sel" id="cfgRuntime">
            <option value="/MT">/MT — static</option>
            <option value="/MD">/MD — dynamic</option>
            <option value="/MTd">/MTd — debug static</option>
            <option value="/MDd">/MDd — debug dynamic</option>
          </select>
        </div>
        <div class="cfg-row">
          <span class="cfg-row-label">Optimization</span>
          <select class="cfg-sel" id="cfgOpt">
            <option value="/O2">/O2 — maximize speed</option>
            <option value="/O1">/O1 — minimize size</option>
            <option value="/Od">/Od — disabled</option>
            <option value="/Ox">/Ox — full optimization</option>
          </select>
        </div>
      </div>
      <div class="cfg-group">
        <div class="cfg-title">BEHAVIOR</div>
        <div class="cfg-row">
          <span class="cfg-row-label">Auto-generate build.bat if missing</span>
          <div class="cfg-tog on" id="togAutoGen" onclick="this.classList.toggle('on')"></div>
        </div>
        <div class="cfg-row">
          <span class="cfg-row-label">Parallel build (/m flag)</span>
          <div class="cfg-tog on" id="togParallel" onclick="this.classList.toggle('on')"></div>
        </div>
      </div>
      <div class="cfg-group">
        <div class="cfg-title">EXTRA INCLUDE PATHS</div>
        <input class="path-input" id="cfgInc" style="width:100%;margin-top:6px"
               placeholder="C:\libs\include;C:\deps\SDL2\include" />
        <div style="font-family:var(--mono);font-size:10px;color:var(--muted);margin-top:6px">
          Semicolon-separated. Added as /I flags.
        </div>
      </div>
      <div class="cfg-group">
        <div class="cfg-title">EXTRA LIB PATHS</div>
        <input class="path-input" id="cfgLib" style="width:100%;margin-top:6px"
               placeholder="C:\libs\lib;C:\deps\SDL2\lib\x64" />
        <div style="font-family:var(--mono);font-size:10px;color:var(--muted);margin-top:6px">
          Semicolon-separated. Added as /LIBPATH flags.
        </div>
      </div>
    </div>

  </div><!-- /content -->
</div><!-- /main -->

<script>
// ── Event bridge from Python ─────────────────────
window.__bdEvent = function(event, data) {
  if (event === 'log')         appendLine(data.text, data.cls);
  if (event === 'done')        onBuildDone(data);
  if (event === 'autofix_done') onAutoFixDone(data);
};

// ── State ────────────────────────────────────────
let diagOpen   = false;
let isBuilding = false;
let buildStart = 0;

const terminal  = document.getElementById('terminal');
const diagBody  = document.getElementById('diagBody');
const diagHdr   = document.getElementById('diagHdr');
const diagChev  = document.getElementById('diagChev');
const sdot      = document.getElementById('sdot');
const stext     = document.getElementById('stext');
const runBtn    = document.getElementById('runBtn');
const buildTime = document.getElementById('buildTime');
const fileCount = document.getElementById('fileCount');
const fileTree  = document.getElementById('fileTree');

// ── Tabs ─────────────────────────────────────────
function showTab(name) {
  ['build','config'].forEach(t => {
    document.getElementById('tab-'+t).style.display = 'none';
    document.getElementById('tab-'+t).classList.remove('show');
    document.getElementById('nav-'+t).classList.remove('active');
  });
  const el = document.getElementById('tab-'+name);
  if (name === 'config') { el.style.display = 'flex'; el.classList.add('show'); }
  else                   { el.style.display = 'flex'; }
  document.getElementById('nav-'+name).classList.add('active');
}

// ── Status ───────────────────────────────────────
function setStatus(s) {
  sdot.className = 'sdot ' + s;
  stext.textContent = {idle:'IDLE',building:'BUILDING...',ok:'BUILD OK',err:'ERRORS FOUND',fixing:'FIXING...'}[s] || s;
}

// ── Terminal ─────────────────────────────────────
function appendLine(text, cls) {
  const cur = terminal.querySelector('.cursor');
  if (cur) cur.remove();
  const span = document.createElement('span');
  span.className = 'l-' + (cls || 'default');
  span.textContent = text;
  terminal.appendChild(span);
  terminal.appendChild(document.createElement('br'));
  const c = document.createElement('span');
  c.className = 'cursor';
  terminal.appendChild(c);
  terminal.scrollTop = terminal.scrollHeight;
}

function clearLog() {
  terminal.innerHTML = '<span class="cursor"></span>';
}

// ── Diagnosis ────────────────────────────────────
function renderDiag(items, success) {
  diagBody.innerHTML = items.map(msg => {
    const isOk     = success && items.length === 1;
    const badgeCls = isOk ? 'badge-ok' : (success ? 'badge-ok' : 'badge-fix');
    const label    = isOk ? 'OK' : 'FIX';
    return `<div class="diag-item">
      <span class="diag-badge ${badgeCls}">${label}</span>
      <span class="diag-text">${msg}</span>
    </div>`;
  }).join('');
}

function toggleDiag() {
  diagOpen = !diagOpen;
  diagBody.style.display = diagOpen ? 'block' : 'none';
  diagChev.innerHTML     = diagOpen ? '&#9660;' : '&#9650;';
  diagHdr.classList.toggle('open', diagOpen);
}

// ── File Tree ─────────────────────────────────────
function renderTree(files, strategy, vcvars, frameworks) {
  const groups = [
    {ext:'.sln',      files:files.sln,                  color:'#4ade80'},
    {ext:'.vcxproj',  files:files.vcxproj,              color:'#60a5fa'},
    {ext:'.cpp/.c',   files:files.cpp.concat(files.c),  color:'#f472b6'},
    {ext:'.h/.hpp',   files:files.headers,              color:'#fbbf24'},
    {ext:'.csproj',   files:files.csproj,               color:'#a78bfa'},
    {ext:'.cs',       files:files.cs,                   color:'#818cf8'},
    {ext:'CMake',     files:files.cmake,                color:'#a78bfa'},
    {ext:'Makefile',  files:files.makefile,             color:'#fb923c'},
  ].filter(g => g.files.length > 0);

  const total = files.cpp.length + files.c.length + files.headers.length +
                files.sln.length + files.vcxproj.length + files.csproj.length +
                files.cs.length  + files.cmake.length   + files.makefile.length;
  fileCount.textContent = total + ' files';

  const treeHtml = groups.map(g => `
    <div class="tree-group">
      <div class="tree-ext">
        <span style="color:${g.color}">${g.ext}</span>
        <span style="color:var(--muted);font-weight:400">(${g.files.length})</span>
      </div>
      ${g.files.map(f => `<div class="tree-file">${f}</div>`).join('')}
    </div>
  `).join('');

  const stratHtml = `
    <div class="tree-strategy">
      Strategy: <span>${strategy}</span><br>
      vcvars64: <span style="color:${vcvars.includes('NOT')?'var(--error)':'var(--accent)'}">${vcvars}</span>
    </div>`;

  let warnHtml = '';
  if (frameworks && frameworks.warnings && frameworks.warnings.length > 0) {
    warnHtml = frameworks.warnings.map(w =>
      `<div class="tree-warn">&#9888; ${w}</div>`
    ).join('');
  }

  fileTree.innerHTML = stratHtml + warnHtml + treeHtml;
}

// ── Get config from settings UI ──────────────────
function getConfig() {
  return {
    std:       document.getElementById('cfgStd').value,
    config:    document.getElementById('cfgConf').value,
    runtime:   document.getElementById('cfgRuntime').value,
    opt:       document.getElementById('cfgOpt').value,
    parallel:  document.getElementById('togParallel').classList.contains('on'),
    extraInc:  document.getElementById('cfgInc').value,
    extraLib:  document.getElementById('cfgLib').value,
  };
}

// ── Helper: render fix action lines ──────────────
function renderFixActions(actions) {
  actions.forEach(line => {
    let cls;
    if (!line) { cls = 'dim'; }
    else if (line.startsWith('[FIXED')) { cls = 'info'; }
    else if (line.startsWith('[ERROR]') || line.startsWith('[SKIP]')) { cls = 'error'; }
    else if (line.startsWith('===')) { cls = 'heading'; }
    else if (line.startsWith('[OK]')) { cls = 'dim'; }
    else { cls = 'default'; }
    appendLine(line, cls);
  });
}

// ── Actions ──────────────────────────────────────
async function doBrowse() {
  const res = JSON.parse(await window.pywebview.api.browse());
  if (res.ok) document.getElementById('pathInput').value = res.path;
}

async function doScan() {
  const folder = document.getElementById('pathInput').value.trim();
  if (!folder) { appendLine('[!] Enter a project path first.', 'error'); return; }
  clearLog();
  appendLine('[*] Scanning: ' + folder, 'info');
  const res = JSON.parse(await window.pywebview.api.scan(folder));
  if (!res.ok) {
    appendLine('[!] ' + res.error, 'error');
    return;
  }
  renderTree(res.files, res.strategy, res.vcvars, res.frameworks);
  const f   = res.files;
  const src = f.cpp.length + f.c.length;
  appendLine(`[+] Found: ${f.sln.length} sln, ${f.vcxproj.length} vcxproj, `+
             `${f.csproj.length} csproj, ${src} source files, ${f.headers.length} headers, `+
             `${f.cs.length} .cs files`, 'info');
  appendLine('[+] Build strategy: ' + res.strategy, 'info');
  if (res.vcvars.includes('NOT')) {
    appendLine('[!] vcvars64.bat not found — install Visual Studio with C++ workload', 'error');
  } else {
    appendLine('[+] MSVC found: ' + res.vcvars, 'dim');
  }
  if (res.frameworks && res.frameworks.warnings.length > 0) {
    res.frameworks.warnings.forEach(w => appendLine('[!] ' + w, 'warn'));
  }
  if (res.frameworks && Object.keys(res.frameworks.frameworks).length > 0) {
    const fwSummary = Object.entries(res.frameworks.frameworks)
      .map(([k,v]) => `${k}: ${v.join(', ')}`).join(' | ');
    appendLine('[~] Target frameworks: ' + fwSummary, 'dim');
  }
}

async function doGenerate() {
  const folder = document.getElementById('pathInput').value.trim();
  if (!folder) { appendLine('[!] Enter a project path first.', 'error'); return; }
  clearLog();
  appendLine('[*] Generating build.bat...', 'info');
  const config = getConfig();
  const res    = JSON.parse(await window.pywebview.api.generate(folder, JSON.stringify(config)));
  if (!res.ok) {
    appendLine('[!] ' + res.error, 'error');
    return;
  }
  appendLine('[+] Generated: ' + res.path, 'info');
  appendLine('', 'dim');
  appendLine('--- build.bat preview ---', 'heading');
  res.preview.split('\n').forEach(l => appendLine(l, 'dim'));
}

async function doFixCS() {
  const folder = document.getElementById('pathInput').value.trim();
  if (!folder) { appendLine('[!] Enter a project path first.', 'error'); return; }
  setStatus('fixing');
  clearLog();
  appendLine('[*] Pass 1 & 2: scanning .csproj files for dead refs and missing ProjectReferences...', 'info');
  const res = JSON.parse(await window.pywebview.api.fix_cs_files(folder));
  if (!res.ok) { appendLine('[!] ' + res.error, 'error'); setStatus('idle'); return; }
  renderFixActions(res.actions);
  const fixed2001 = res.actions.filter(l => l.startsWith('[FIXED-CS2001]')).length;
  const fixed0246 = res.actions.filter(l => l.startsWith('[FIXED-CS0246]')).length;
  appendLine('', 'dim');
  if (fixed2001 + fixed0246 > 0) {
    if (fixed2001) appendLine(`[+] CS2001: removed ${fixed2001} dead Compile reference(s).`, 'info');
    if (fixed0246) appendLine(`[+] CS0246: added ${fixed0246} missing ProjectReference(s).`, 'info');
    appendLine('[+] Re-run your build now.', 'info');
  } else {
    appendLine('[+] No auto-fixable issues found.', 'dim');
  }
  setStatus('idle');
}

async function doFixDupes() {
  const folder = document.getElementById('pathInput').value.trim();
  if (!folder) { appendLine('[!] Enter a project path first.', 'error'); return; }
  setStatus('fixing');
  clearLog();
  appendLine('[*] Pass 3: scanning for duplicate references and empty ItemGroups...', 'info');
  const res = JSON.parse(await window.pywebview.api.fix_duplicate_refs(folder));
  if (!res.ok) { appendLine('[!] ' + res.error, 'error'); setStatus('idle'); return; }
  renderFixActions(res.actions);
  const fixed = res.actions.filter(l => l.startsWith('[FIXED')).length;
  appendLine('', 'dim');
  appendLine(fixed > 0
    ? `[+] Fixed ${fixed} issue(s). Re-run your build now.`
    : '[+] No duplicates or empty groups found.', fixed > 0 ? 'info' : 'dim');
  setStatus('idle');
}

async function doFixHeaders() {
  const folder = document.getElementById('pathInput').value.trim();
  if (!folder) { appendLine('[!] Enter a project path first.', 'error'); return; }
  setStatus('fixing');
  clearLog();
  appendLine('[*] Scanning C++ headers for missing include guards...', 'info');
  const res = JSON.parse(await window.pywebview.api.fix_cpp_headers(folder));
  if (!res.ok) { appendLine('[!] ' + res.error, 'error'); setStatus('idle'); return; }
  renderFixActions(res.actions);
  const fixed = res.actions.filter(l => l.startsWith('[FIXED-HDR]')).length;
  appendLine('', 'dim');
  appendLine(fixed > 0
    ? `[+] Added #pragma once to ${fixed} header(s).`
    : '[+] All headers already have include guards.', fixed > 0 ? 'info' : 'dim');
  setStatus('idle');
}

async function doFixVcxproj() {
  const folder = document.getElementById('pathInput').value.trim();
  if (!folder) { appendLine('[!] Enter a project path first.', 'error'); return; }
  setStatus('fixing');
  clearLog();
  appendLine('[*] Pass 9: syncing .vcxproj item lists against files on disk...', 'info');
  appendLine('[~] Adds missing <ClCompile> / <ClInclude> entries, removes dead ones.', 'dim');
  appendLine('', 'dim');
  const res = JSON.parse(await window.pywebview.api.fix_vcxproj_items(folder));
  if (!res.ok) { appendLine('[!] ' + res.error, 'error'); setStatus('idle'); return; }
  renderFixActions(res.actions);
  const added = res.actions.filter(l => l.startsWith('[FIXED-ADD]')).length;
  const dead  = res.actions.filter(l => l.startsWith('[FIXED-DEAD]')).length;
  const skipped = res.actions.filter(l => l.startsWith('[SKIP]')).length;
  appendLine('', 'dim');
  if (added > 0 || dead > 0) {
    const parts = [];
    if (added > 0) parts.push(`added ${added} missing entry/entries`);
    if (dead  > 0) parts.push(`removed ${dead} dead entry/entries`);
    appendLine(`[+] .vcxproj synced: ${parts.join(', ')}. Re-run your build now.`, 'info');
  } else if (skipped > 0) {
    appendLine('[!] No .vcxproj files found — check your project path.', 'warn');
  } else {
    appendLine('[+] All .vcxproj item references are already in sync with disk.', 'dim');
  }
  setStatus('idle');
}

async function doFixIncludes() {
  const folder = document.getElementById('pathInput').value.trim();
  if (!folder) { appendLine('[!] Enter a project path first.', 'error'); return; }
  setStatus('fixing');
  clearLog();
  appendLine('[*] Scanning for missing C++ include directories (C1083)...', 'info');
  appendLine('[~] Uses last build output to target specific missing headers.', 'dim');
  const res = JSON.parse(await window.pywebview.api.fix_cpp_includes(folder));
  if (!res.ok) { appendLine('[!] ' + res.error, 'error'); setStatus('idle'); return; }
  renderFixActions(res.actions);
  const fixed = res.actions.filter(l => l.startsWith('[FIXED-INCS]')).length;
  const warned = res.actions.filter(l => l.startsWith('[WARN]')).length;
  appendLine('', 'dim');
  if (fixed > 0) {
    appendLine(`[+] Patched ${fixed} .vcxproj file(s) with new include paths. Re-run your build now.`, 'info');
  } else if (warned > 0) {
    appendLine(`[!] ${warned} header(s) could not be located — add them manually or check dependencies.`, 'warn');
  } else {
    appendLine('[+] All required include paths already present.', 'dim');
  }
  setStatus('idle');
}

async function doFixLuau() {
  const folder = document.getElementById('pathInput').value.trim();
  if (!folder) { appendLine('[!] Enter a project path first.', 'error'); return; }
  setStatus('fixing');
  clearLog();
  appendLine('[*] Luau dependency fix — this may take a while if cloning is needed...', 'info');
  appendLine('[~] Will init git submodule or clone luau-lang/luau, then patch .vcxproj.', 'dim');
  appendLine('', 'dim');
  // Lines stream live via the log event; final actions come back in res.actions
  const res = JSON.parse(await window.pywebview.api.fix_luau(folder));
  if (!res.ok) { appendLine('[!] ' + res.error, 'error'); setStatus('idle'); return; }
  const fixed  = res.actions.filter(l => l.startsWith('[FIXED-LUAU]')).length;
  const errors = res.actions.filter(l => l.startsWith('[ERROR]')).length;
  const warned = res.actions.filter(l => l.startsWith('[WARN]')).length;
  appendLine('', 'dim');
  if (errors > 0) {
    appendLine('[!] One or more steps failed — check output above.', 'error');
  } else if (fixed > 0) {
    appendLine(`[+] Luau fixed: ${fixed} .vcxproj patched. Re-run your build now.`, 'info');
  } else if (warned > 0) {
    appendLine('[!] Could not fully auto-fix — see warnings above.', 'warn');
  } else {
    appendLine('[+] Luau include paths already in order.', 'dim');
  }
  setStatus('idle');
}

async function doDotnetRestore() {
  const folder = document.getElementById('pathInput').value.trim();
  if (!folder) { appendLine('[!] Enter a project path first.', 'error'); return; }
  setStatus('fixing');
  clearLog();
  appendLine('[*] Running dotnet restore — this may take a moment...', 'info');
  const res = JSON.parse(await window.pywebview.api.run_dotnet_restore(folder));
  (res.output || '').split('\n').forEach(l => {
    const lo = l.toLowerCase();
    if (!l.trim()) return;
    const cls = (lo.includes('error') || lo.includes('fail')) ? 'error'
              : lo.includes('warn') ? 'warn'
              : (lo.includes('restored') || lo.includes('success') || lo.includes('installing')) ? 'info'
              : 'dim';
    appendLine(l.trim(), cls);
  });
  appendLine('', 'dim');
  appendLine(res.ok
    ? '[+] dotnet restore succeeded. Re-run your build now.'
    : '[!] Restore failed — check output above.', res.ok ? 'info' : 'error');
  setStatus('idle');
}

async function doFixAll() {
  const folder = document.getElementById('pathInput').value.trim();
  if (!folder) { appendLine('[!] Enter a project path first.', 'error'); return; }
  setStatus('fixing');
  clearLog();
  appendLine('[*] Running ALL C# auto-fix passes...', 'purple');
  appendLine('', 'dim');
  const res = JSON.parse(await window.pywebview.api.fix_all(folder));
  if (!res.ok) { appendLine('[!] ' + res.error, 'error'); setStatus('idle'); return; }
  renderFixActions(res.actions);
  const fixed = res.actions.filter(l => l.startsWith('[FIXED')).length;
  appendLine('', 'dim');
  appendLine(fixed > 0
    ? `[+] Total fixed: ${fixed} issue(s) across all passes. Re-run your build now.`
    : '[+] No auto-fixable issues found across all passes.', fixed > 0 ? 'info' : 'dim');
  setStatus('idle');
}

async function doRunBuild() {
  if (isBuilding) return;
  const folder = document.getElementById('pathInput').value.trim();
  if (!folder) { appendLine('[!] Enter a project path first.', 'error'); return; }

  isBuilding = true;
  runBtn.disabled   = true;
  runBtn.textContent = '\u23F3 BUILDING';
  setStatus('building');
  clearLog();
  diagBody.innerHTML = '';
  buildTime.textContent = '';
  buildStart = Date.now();

  appendLine('[*] Starting build: ' + folder, 'info');
  appendLine('', 'dim');

  const config = getConfig();
  await window.pywebview.api.run_build(folder, JSON.stringify(config));
}

function onBuildDone(data) {
  const elapsed = ((Date.now() - buildStart) / 1000).toFixed(2);
  buildTime.textContent = elapsed + 's';

  appendLine('', 'dim');
  appendLine('=== DIAGNOSIS ===', 'heading');
  data.diag.forEach(msg => appendLine('[+] ' + msg, data.success ? 'info' : 'warn'));

  if (data.success && data.exePath) {
    appendLine('', 'dim');
    appendLine('\u2714 BUILD SUCCEEDED', 'info');
    appendLine('\u2192 EXE: ' + data.exePath, 'info');
    if (data.sha256) appendLine('[SHA256] ' + data.sha256, 'dim');
  } else if (data.success) {
    appendLine('', 'dim');
    appendLine('\u2714 BUILD SUCCEEDED (check project output folder for .exe)', 'info');
  }

  if (data.hasLuau) {
    appendLine('', 'dim');
    appendLine('[!] Luau files missing — click FIX LUAU to clone the repo, copy missing .cpp sources, patch include paths, and update your .vcxproj.', 'warn');
  } else if (data.hasC1083) {
    appendLine('', 'dim');
    appendLine('[!] C1083 detected — click FIX INCS to auto-patch include paths in your .vcxproj.', 'warn');
  }
  if (data.hasLnk) {
    appendLine('', 'dim');
    appendLine('[!] LNK errors detected — click FIX LIBS to auto-find and inject missing .lib paths.', 'warn');
  }
  if (data.hasPch) {
    appendLine('', 'dim');
    appendLine('[!] C1010 detected — click FIX PCH to create a PCH stub or disable precompiled headers.', 'warn');
  }

  if (!data.success) {
    appendLine('', 'dim');
    appendLine('[TIP] Click \u25b6\u25b6 AUTO-FIX to automatically detect errors and retry the build.', 'dim');
  }

  renderDiag(data.diag, data.success);
  if (!diagOpen) toggleDiag();

  setStatus(data.success ? 'ok' : 'err');
  isBuilding = false;
  runBtn.disabled   = false;
  runBtn.textContent = '\u25B6 RUN BUILD';
}

function onAutoFixDone(data) {
  const elapsed = ((Date.now() - buildStart) / 1000).toFixed(2);
  buildTime.textContent = elapsed + 's';

  appendLine('', 'dim');
  appendLine('═══ AUTO-FIX LOOP COMPLETE ═══', 'heading');
  if (data.success) {
    appendLine(`\u2714 BUILD SUCCEEDED after ${data.attempts} attempt(s).`, 'info');
    if (data.exePath) appendLine('\u2192 EXE: ' + data.exePath, 'info');
    if (data.sha256)  appendLine('[SHA256] ' + data.sha256, 'dim');
  } else {
    appendLine(`\u2716 BUILD FAILED after ${data.attempts} attempt(s) — manual intervention needed.`, 'error');
    appendLine('[TIP] Check the output above for unfixed errors, or use individual FIX buttons.', 'warn');
  }

  data.diag.forEach(msg => appendLine('[+] ' + msg, data.success ? 'info' : 'warn'));

  renderDiag(data.diag, data.success);
  if (!diagOpen) toggleDiag();

  setStatus(data.success ? 'ok' : 'err');
  isBuilding = false;
  runBtn.disabled   = false;
  runBtn.textContent = '\u25B6 RUN BUILD';
}

async function doFixLibs() {
  const folder = document.getElementById('pathInput').value.trim();
  if (!folder) { appendLine('[!] Enter a project path first.', 'error'); return; }
  setStatus('fixing');
  clearLog();
  appendLine('[*] Pass 10: scanning for missing .lib files (LNK errors)...', 'info');
  appendLine('[~] Will search project tree and SDK paths for matching .lib files.', 'dim');
  appendLine('', 'dim');
  const res = JSON.parse(await window.pywebview.api.fix_missing_libs(folder));
  if (!res.ok) { appendLine('[!] ' + res.error, 'error'); setStatus('idle'); return; }
  renderFixActions(res.actions);
  const fixed  = res.actions.filter(l => l.startsWith('[FIXED-LIB]')).length;
  const warned = res.actions.filter(l => l.startsWith('[WARN]')).length;
  appendLine('', 'dim');
  if (fixed > 0) {
    appendLine(`[+] Patched ${fixed} .vcxproj entry/entries with lib paths. Re-run build now.`, 'info');
  } else if (warned > 0) {
    appendLine('[!] Some .lib files could not be found — add them manually or install the dependency.', 'warn');
  } else {
    appendLine('[+] All required lib paths already present.', 'dim');
  }
  setStatus('idle');
}

async function doFixPch() {
  const folder = document.getElementById('pathInput').value.trim();
  if (!folder) { appendLine('[!] Enter a project path first.', 'error'); return; }
  setStatus('fixing');
  clearLog();
  appendLine('[*] Pass 11: checking precompiled header (PCH) configuration...', 'info');
  const res = JSON.parse(await window.pywebview.api.fix_pch(folder));
  if (!res.ok) { appendLine('[!] ' + res.error, 'error'); setStatus('idle'); return; }
  renderFixActions(res.actions);
  const fixed  = res.actions.filter(l => l.startsWith('[FIXED-PCH]')).length;
  appendLine('', 'dim');
  appendLine(fixed > 0
    ? `[+] PCH fixed: ${fixed} action(s) taken. Re-run build now.`
    : '[+] PCH configuration looks fine.', fixed > 0 ? 'info' : 'dim');
  setStatus('idle');
}

async function doAutoFixLoop() {
  if (isBuilding) return;
  const folder = document.getElementById('pathInput').value.trim();
  if (!folder) { appendLine('[!] Enter a project path first.', 'error'); return; }

  isBuilding = true;
  runBtn.disabled   = true;
  runBtn.textContent = '\u23F3 BUILDING';
  setStatus('building');
  clearLog();
  diagBody.innerHTML = '';
  buildTime.textContent = '';
  buildStart = Date.now();

  appendLine('[*] AUTO-FIX LOOP started — will build, detect errors, fix, and retry.', 'info');
  appendLine('[~] Max 3 fix+retry attempts before giving up.', 'dim');
  appendLine('', 'dim');

  const config = getConfig();
  await window.pywebview.api.auto_fix_loop(folder, JSON.stringify(config), 3);
}

function doClear() {
  clearLog();
  diagBody.innerHTML    = '';
  buildTime.textContent = '';
  setStatus('idle');
}
</script>
</body>
</html>
"""


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────

def main():
    api    = Api()
    window = webview.create_window(
        APP_TITLE,
        html=HTML,
        js_api=api,
        width=1160,
        height=720,
        min_size=(900, 580),
        background_color="#0e0f11",
    )
    api.set_window(window)
    webview.start(debug=False)


if __name__ == "__main__":
  main()
