/* Шаблон програвача для CPUlator. Ролик застосунок дописує окремим сегментом
   за адресою __blob_base: там його шукає програвач (BLOB_BASE у player_cpulator.c). */
ENTRY(_start)
__blob_base = 0xC0100000;
SECTIONS
{
    . = 0x00000000;
    .text   : { *crt0.o(.text) *(.text*) *(.rodata*) }
    .data   : { *(.data*) }
    . = ALIGN(4);
    .bss    : { __bss_start__ = .; *(.bss*) *(COMMON) . = ALIGN(4); __bss_end__ = .; }
}
