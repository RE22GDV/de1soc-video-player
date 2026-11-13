/* =====================================================================
 *  Мінімальна перевірка тракту VGA перед запуском програвача
 * ---------------------------------------------------------------------
 *  Це та сама перша перевірка, з якої слід починати: доки на екрані не
 *  з'явиться правильна картинка, писати решту немає сенсу. Програма
 *  нічого не розпаковує й не читає з пам'яті ролика - вона лише малює
 *  у буфер пікселів кілька смуг і перемикає буфери.
 *
 *  ЩО МАЄ БУТИ НА ЕКРАНІ, згори вниз:
 *
 *    1. чиста червона смуга      RGB565 = 0xF800
 *    2. чиста зелена смуга       RGB565 = 0x07E0
 *    3. чиста синя смуга         RGB565 = 0x001F
 *    4. білий - чорний градієнт
 *    5. шахівниця 8x8 чорно-білих клітинок
 *
 *  НАВІЩО САМЕ ТАКІ СМУГИ
 *  Формат RGB565 легко переплутати, записавши байти у зворотному
 *  порядку: тоді червоний 0xF800 перетвориться на 0x00F8, тобто на
 *  темно-синій із зеленуватим відтінком. Тому перші дві смуги обрано
 *  чистими червоною й синьою: якщо вони помінялися місцями або
 *  потьмяніли, порядок байтів переплутано. Градієнт показує, чи не
 *  загублено молодші розряди, а шахівниця - чи правильний крок рядка
 *  (1024 байти, а не 640): при неправильному кроці клітинки
 *  перекосяться в діагональ.
 *
 *  KEY0 перемикає буфери місцями. Обидва буфери містять однакову
 *  картинку, тому зображення не змінюється - але якщо натискання
 *  призводить до зникнення картинки, керування буферами працює не так,
 *  як очікується.
 * ===================================================================== */

#define LEDR_BASE        0xFF200000
#define KEY_BASE         0xFF200050
#define PIXEL_CTRL_BASE  0xFF203020
#define STATUS_S_BIT     0x1

#define SDRAM_BASE       0xC0000000
#define FRONT_BUFFER     (SDRAM_BASE + 0x00000000)
#define BACK_BUFFER      (SDRAM_BASE + 0x00040000)

#define WIDTH            320
#define HEIGHT           240
#define ROW_BYTES        1024

static void put_pixel(unsigned int buffer, int x, int y, unsigned short c) {
    volatile unsigned short * p =
        (unsigned short *)(buffer + ((unsigned int)y << 10) + ((unsigned int)x << 1));
    *p = c;
}

static void draw_pattern(unsigned int buffer) {
    int x, y;

    for (y = 0; y < HEIGHT; y++) {
        for (x = 0; x < WIDTH; x++) {
            unsigned short c;

            if (y < 40) {
                c = 0xF800;                  /* чистий червоний */
            } else if (y < 80) {
                c = 0x07E0;                  /* чистий зелений  */
            } else if (y < 120) {
                c = 0x001F;                  /* чистий синій    */
            } else if (y < 180) {
                /* градієнт: 32 рівні сірого зліва направо */
                int level = (x * 32) / WIDTH;         /* 0..31 */
                c = (unsigned short)((level << 11) |
                                     ((level * 2) << 5) |
                                     level);
            } else {
                /* шахівниця з клітинкою 8x8 */
                c = (((x >> 3) + (y >> 3)) & 1) ? 0xFFFF : 0x0000;
            }
            put_pixel(buffer, x, y, c);
        }
    }
}

int main(void) {
    volatile unsigned int * ledr = (unsigned int *)LEDR_BASE;
    volatile unsigned int * pix  = (unsigned int *)PIXEL_CTRL_BASE;
    volatile int          * key  = (int *)KEY_BASE;

    unsigned int front = FRONT_BUFFER;
    unsigned int back  = BACK_BUFFER;

    /* Малюємо однакову картинку в обидва буфери: перемикання тоді
       нічого не змінює на екрані, і будь-яке мигання означає помилку. */
    draw_pattern(front);
    draw_pattern(back);

    *(pix + 1) = back;               /* BackBuffer */
    *(pix + 0) = 1;                  /* запит обміну */
    while (*(pix + 3) & STATUS_S_BIT)
        ;

    *ledr = 0x1;

    while (1) {
        if (*key) {
            unsigned int t;
            while (*key)
                ;                    /* дочекатися відпускання */

            t = front; front = back; back = t;
            *(pix + 1) = back;
            *(pix + 0) = 1;
            while (*(pix + 3) & STATUS_S_BIT)
                ;
            *ledr ^= 0x2;            /* видима ознака, що обмін стався */
        }
    }
}
