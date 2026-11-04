@echo off
rem play.cmd: play.ps1 from cmd or a double click. Same arguments, e.g.
rem   play.cmd -ExeFS D:\pokopia\ExeFS -Dll D:\pokopia\game.dll -RomFS D:\pokopia\RomFS.img -Save D:\pokopia\play-save
rem Link Play between two of our builds: add -CoopHost 47600 on the host, -CoopJoin 127.0.0.1:47600 on the visitor (its own -Save and -Work; -CoopTcp through a tunnel)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0play.ps1" %*
exit /b %ERRORLEVEL%
