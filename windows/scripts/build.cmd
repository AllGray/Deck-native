@echo off
rem build.cmd [-j N] [Debug|Release]: configures (first time) and builds into build\<config> with clang++ and the Visual Studio C++
rem library. Gentle by default: 4 jobs at low priority (this PC is an i9-13900KF without Intel's microcode fix; long all-core loads crash it).
rem No parenthesised blocks around paths: "(x86)" in a path ends a cmd block early.
setlocal
rem Before the argument loop: shift moves %0 too, so %~dp0 read after it is the current directory
set "ROOT=%~dp0.."
set "JOBS=4"
set "CONFIG=Release"
:args
if "%~1"=="" goto run
if "%~1"=="-j" goto jobs
set "CONFIG=%~1"
shift
goto args
:jobs
set "JOBS=%~2"
shift
shift
goto args
:run
set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
if not exist "%VCVARS%" goto novs
call "%VCVARS%" >nul 2>nul
set "PATH=C:\Program Files\LLVM\bin;C:\msys64\mingw64\bin;%PATH%"
set "B=%ROOT%\build\%CONFIG%"
if exist "%B%\build.ninja" goto build
cmake -S "%ROOT%" -B "%B%" -G Ninja -DCMAKE_BUILD_TYPE=%CONFIG% -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++
if errorlevel 1 exit /b 1
:build
start "" /b /low /wait cmake --build "%B%" -j %JOBS%
exit /b %ERRORLEVEL%
:novs
echo Visual Studio 2022 Build Tools not found: %VCVARS%
exit /b 1
