/* Мінімальний старт для CPUlator: стек, обнулення .bss, виклик main. */
        .text
        .global _start
_start:
        ldr     sp, =0x00200000
        ldr     r0, =__bss_start__
        ldr     r1, =__bss_end__
        mov     r2, #0
1:      cmp     r0, r1
        strlo   r2, [r0], #4
        blo     1b
        bl      main
2:      b       2b
