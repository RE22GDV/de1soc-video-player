@echo off
rem Перезбирання шаблону build\player_cpulator.elf для CPUlator.
rem Потрібен набір інструментів arm-altera-eabi з Intel FPGA Monitor Program 18.1.
rem Для звичайної роботи застосунку перезбирати не треба: шаблон уже в репозиторії.
chcp 65001 >nul
cd /d "%~dp0"
set TOOLS=C:\intelFPGA_lite\18.1\University_Program\Monitor_Program\arm_tools\baremetal\bin
if not exist "%TOOLS%\arm-altera-eabi-gcc.exe" (
    echo Не знайдено %TOOLS%\arm-altera-eabi-gcc.exe
    echo Встановіть Intel FPGA Monitor Program 18.1 або вкажіть шлях у змінній TOOLS.
    pause
    exit /b 1
)
set PATH=%TOOLS%;%PATH%
if not exist build mkdir build
arm-altera-eabi-gcc -O2 -mcpu=cortex-a9 -marm -ffreestanding -Wall -c player_cpulator.c -o build\player.o || goto fail
arm-altera-eabi-as -mcpu=cortex-a9 crt0.s -o build\crt0.o || goto fail
arm-altera-eabi-gcc -nostdlib -T player_cpulator.ld build\crt0.o build\player.o -lgcc -o build\player_cpulator.elf || goto fail
arm-altera-eabi-size build\player_cpulator.elf
echo Готово: build\player_cpulator.elf
exit /b 0
:fail
echo Помилка збирання
exit /b 1
