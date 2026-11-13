/* =====================================================================
 *  Програвач відео зі звуком для DE1-SoC
 * ---------------------------------------------------------------------
 *  Виводить на вихід VGA заздалегідь запакований ролик 320x240 у
 *  форматі RGB565 з частотою 24 кадри на секунду, а на лінійний вихід -
 *  звукову доріжку 48 кГц. Дані лежать одним блоком у SDRAM, куди їх
 *  заливають окремо; програма лише розпаковує й видає їх далі.
 *
 *  КЕРУВАННЯ
 *    KEY0     - пауза; повторне натискання продовжує з того ж місця
 *    KEY1     - почати з початку (працює і на паузі, і під час показу)
 *    SW[0]    - зациклювання: у верхньому положенні після останнього
 *               кадру показ починається спочатку, у нижньому - стоп
 *    SW[2:1]  - гучність: 00 повна, 01 тихіше вдвічі, 10 вчетверо,
 *               11 увосьмеро
 *    HEX5-0   - номер поточного кадру, шістнадцятковий
 *    LEDR0    - показ іде
 *    LEDR1    - пауза
 *    LEDR2    - звук працює
 *    LEDR9    - зрив дедлайну: кадр не вклався в 41,667 мс
 *
 *  LEDR9 не гасне до наступного натискання KEY1, щоб зрив було видно,
 *  навіть якщо він стався один раз посеред ролика.
 * ---------------------------------------------------------------------
 *  РОЗКЛАДКА ПАМ'ЯТІ
 *
 *  Контролер пікселів не під'єднаний до DDR3 - це прямо сказано в
 *  описі системи DE1-SoC Computer. Тому буфери зображення лежать у
 *  SDRAM, куди контролер доступ має. Там же лежить і запакований
 *  ролик: заливати його туди можна через міст JTAG-to-FPGA, який до
 *  SDRAM під'єднаний.
 *
 *    0xC0000000  передній буфер         245 760 байтів (240 рядків по 1 КіБ)
 *    0xC0040000  задній буфер           245 760 байтів
 *    0xC0100000  запакований ролик      до 63 МіБ
 *
 *  Робочий кадр, у якому збирається зображення перед копіюванням у
 *  задній буфер, лежить у DDR3 разом із самою програмою.
 *
 *  Навіщо потрібен робочий кадр, а не розпаковка одразу в задній
 *  буфер: при подвійній буферизації задній буфер містить кадр не
 *  попередній, а позаминулий, тоді як дельти пораховано відносно
 *  попереднього. Тримати окремий робочий кадр простіше й дешевше, ніж
 *  вводити третій буфер.
 * ---------------------------------------------------------------------
 *  АДРЕСИ ПІКСЕЛІВ
 *  Піксель x,y лежить за адресою base + (y << 10) + (x << 1). Рядок
 *  займає 1024 байти, хоча корисних у ньому 640: така адресація
 *  дозволяє контролеру обходитися зсувами замість множення.
 * ===================================================================== */

/* --- пристрої системи ------------------------------------------------ */
#define LEDR_BASE        0xFF200000
#define HEX3_HEX0_BASE   0xFF200020
#define HEX5_HEX4_BASE   0xFF200030
#define SW_BASE          0xFF200040
#define KEY_BASE         0xFF200050

/* контролер буферів пікселів, розділ 4.2.3 опису DE1-SoC Computer */
#define PIXEL_CTRL_BASE  0xFF203020      /* +0 Buffer, +4 BackBuffer,
                                            +8 Resolution, +C Status   */
#define STATUS_S_BIT     0x1             /* розряд 0 регістра стану    */

/* звуковий порт, розділ 4.1 опису; адресу звірено з картою адрес
   згенерованої системи, а не взято з голови */
#define AUDIO_BASE       0xFF203040      /* +0 Control, +4 Fifospace,
                                            +8 Leftdata, +C Rightdata  */
#define AUDIO_FIFO_DEPTH 128             /* черга на 128 відліків      */

/* приватний таймер ядра A9: 200 МГц, лічить униз */
#define PRIV_TIMER_BASE  0xFFFEC600      /* +0 Load, +4 Counter,
                                            +8 Control, +C Status      */
#define TIMER_HZ         200000000u

/* --- розкладка пам'яті ----------------------------------------------- */
#define SDRAM_BASE       0xC0000000
#define FRONT_BUFFER     (SDRAM_BASE + 0x00000000)
#define BACK_BUFFER      (SDRAM_BASE + 0x00040000)
#define BLOB_BASE        (SDRAM_BASE + 0x00100000)
#define BLOB_MAX         0x03F00000      /* скільки лишилося до кінця SDRAM */

/* --- параметри зображення -------------------------------------------- */
#define WIDTH            320
#define HEIGHT           240
#define PIXELS           (WIDTH * HEIGHT)
#define ROW_BYTES        1024            /* крок рядка в буфері пікселів */

#define BLOB_MAGIC       0x56314544u     /* "DE1V" у порядку байтів ARM  */
#define MAX_SPF          2048            /* стеля відліків на кадр       */

/* --- заголовок запакованого ролика ----------------------------------- */
struct blob_header {
    unsigned int   magic;
    unsigned short version;
    unsigned short flags;            /* розряд 0: у блоці є звук */
    unsigned short width;
    unsigned short height;
    unsigned short fps;
    unsigned short keyint;
    unsigned int   frames;
    unsigned int   index_off;
    unsigned int   data_off;
    unsigned int   blob_size;
    unsigned int   audio_off;        /* далі - тільки у версії 2 */
    unsigned int   audio_block;
    unsigned int   audio_rate;
    unsigned short audio_spf;        /* відліків на кадр на канал */
    unsigned short audio_ch;
};

struct frame_entry {
    unsigned int offset;
    unsigned int size;               /* старший розряд = опорний кадр */
};

/* Робочий кадр. Лежить у DDR3 разом із програмою: сюди розпаковується
   зображення, звідки потім копіюється в задній буфер. */
static unsigned short work[PIXELS];

/* --- сім сегментів --------------------------------------------------- */
/* Верхній рівень ієрархії інвертує ці розряди, тому тут одиниця
   означає «сегмент світиться». */
static const unsigned char seg7[16] = {
    0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07,
    0x7F, 0x6F, 0x77, 0x7C, 0x39, 0x5E, 0x79, 0x71
};

static void show_number(unsigned int v) {
    volatile unsigned int * hex30 = (unsigned int *)HEX3_HEX0_BASE;
    volatile unsigned int * hex54 = (unsigned int *)HEX5_HEX4_BASE;
    unsigned int lo = 0, hi = 0;
    int i;

    for (i = 0; i < 4; i++)
        lo |= ((unsigned int)seg7[(v >> (4 * i)) & 0xF]) << (8 * i);
    for (i = 0; i < 2; i++)
        hi |= ((unsigned int)seg7[(v >> (16 + 4 * i)) & 0xF]) << (8 * i);

    *hex30 = lo;
    *hex54 = hi;
}

/* --- таймер ---------------------------------------------------------- */
static void timer_start(void) {
    volatile unsigned int * t = (unsigned int *)PRIV_TIMER_BASE;
    *(t + 0) = 0xFFFFFFFFu;          /* Load: лічити від максимуму      */
    *(t + 2) = 0x3;                  /* E = 1, A = 1 (автоперезавантаження) */
}

static unsigned int timer_now(void) {
    volatile unsigned int * t = (unsigned int *)PRIV_TIMER_BASE;
    return *(t + 1);                 /* Counter                         */
}

/* --- звук ------------------------------------------------------------ */
/*
 *  Доріжка запакована тим самим блоком, що й відео, окремим шматком на
 *  кожен кадр: 2000 відліків на канал при 48 кГц і 24 кадрах на
 *  секунду. Кожен шматок самодостатній - на його початку лежить стан
 *  передбачувача, - тому перезапуск і зациклювання нічого не ламають.
 *
 *  Стиснення - IMA ADPCM, чотири розряди на відлік. Це не ентропійне
 *  кодування, а звичайна арифметика з таблицею: близько десятка
 *  операцій на відлік, тобто десятки тисяч тактів на кадр проти
 *  33 мільйонів, які дає дедлайн.
 */
static const short step_tab[89] = {
        7,     8,     9,    10,    11,    12,    13,    14,
       16,    17,    19,    21,    23,    25,    28,    31,
       34,    37,    41,    45,    50,    55,    60,    66,
       73,    80,    88,    97,   107,   118,   130,   143,
      157,   173,   190,   209,   230,   253,   279,   307,
      337,   371,   408,   449,   494,   544,   598,   658,
      724,   796,   876,   963,  1060,  1166,  1282,  1411,
     1552,  1707,  1878,  2066,  2272,  2499,  2749,  3024,
     3327,  3660,  4026,  4428,  4871,  5358,  5894,  6484,
     7132,  7845,  8630,  9493, 10442, 11487, 12635, 13899,
    15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794,
    32767
};

static const signed char index_tab[16] = {
    -1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8
};

static short a_left[MAX_SPF];
static short a_right[MAX_SPF];
static int   a_pos;              /* скільки відліків уже віддано       */
static int   a_count;            /* скільки їх у поточному кадрі       */
static int   a_shift;            /* 16 мінус послаблення гучності      */

/* Розгортає один канал шматка. Чотири байти заголовка - це стан, з
   якого треба почати: два байти передбачувача і один індекс кроку. */
static void adpcm_decode(const unsigned char * head,
                         const unsigned char * nib,
                         int n, short * out) {
    int pred  = (int)(short)((unsigned short)head[0]
                             | ((unsigned short)head[1] << 8));
    int index = head[2];
    int i;

    for (i = 0; i < n; i++) {
        int code = (i & 1) ? (nib[i >> 1] >> 4) : (nib[i >> 1] & 0xF);
        int step = step_tab[index];
        int diff = step >> 3;

        if (code & 4) diff += step;
        if (code & 2) diff += step >> 1;
        if (code & 1) diff += step >> 2;

        pred = (code & 8) ? (pred - diff) : (pred + diff);
        if (pred > 32767)       pred = 32767;
        else if (pred < -32768) pred = -32768;

        index += index_tab[code];
        if (index < 0)       index = 0;
        else if (index > 88) index = 88;

        out[i] = (short)pred;
    }
}

/* Чи є звуковий порт у прошитій системі. Опис каже: після очищення
   черг Fifospace показує 128 вільних місць у кожному вихідному
   напрямку. Якщо ядра немає, звертання йде в порожнечу й дає нулі -
   саме за цим його й відрізняємо, замість того щоб вгадувати. */
static int audio_probe(void) {
    volatile unsigned int * a = (unsigned int *)AUDIO_BASE;
    unsigned int space;

    *(a + 0) = 0xC;                  /* CR і CW: очистити обидві черги  */
    *(a + 0) = 0x0;
    space = *(a + 1);
    return (((space >> 24) & 0xFF) == AUDIO_FIFO_DEPTH)
        && (((space >> 16) & 0xFF) == AUDIO_FIFO_DEPTH);
}

static void audio_clear(void) {
    volatile unsigned int * a = (unsigned int *)AUDIO_BASE;
    *(a + 0) = 0xC;
    *(a + 0) = 0x0;
}

/* Доливає у черги стільки відліків, скільки туди влізе. Викликається
   всюди, де програма чимось зайнята надовго: черга тримає лише 128
   відліків, тобто 2,7 мс звучання, а розпаковка кадру може тривати
   довше. */
static void audio_pump(void) {
    volatile unsigned int * a = (unsigned int *)AUDIO_BASE;
    unsigned int space, wsl, wsr, n;

    if (a_pos >= a_count)
        return;

    space = *(a + 1);
    wsl = (space >> 24) & 0xFF;
    wsr = (space >> 16) & 0xFF;
    n = (wsl < wsr) ? wsl : wsr;

    while (n-- && a_pos < a_count) {
        *(a + 2) = ((unsigned int)(int)a_left[a_pos])  << a_shift;
        *(a + 3) = ((unsigned int)(int)a_right[a_pos]) << a_shift;
        a_pos++;
    }
}

/* --- буфери пікселів -------------------------------------------------- */
static void pixel_set_back(unsigned int addr) {
    volatile unsigned int * p = (unsigned int *)PIXEL_CTRL_BASE;
    *(p + 1) = addr;                 /* BackBuffer                      */
}

/* Обмін переднього й заднього буферів відбувається не одразу, а в
   момент кадрової синхронізації: запис одиниці в регістр Buffer лише
   замовляє його й піднімає розряд S у регістрі стану, а апаратура
   скидає S, коли обмін відбувся.

   Тому замовлення й очікування розділено. Замовляти треба одразу, як
   кадр готовий, а чекати - в самому кінці, після витримки часу.
   Якщо робити навпаки, до періоду кадру додасться ще й очікування
   синхронізації - у середньому 8,3 мс при 60 Гц, тобто замість
   24 кадрів на секунду вийшло б близько 20. */
static void pixel_swap_request(void) {
    volatile unsigned int * p = (unsigned int *)PIXEL_CTRL_BASE;
    *(p + 0) = 1;
}

static void pixel_swap_wait(void) {
    volatile unsigned int * p = (unsigned int *)PIXEL_CTRL_BASE;
    while (*(p + 3) & STATUS_S_BIT)
        audio_pump();
}

/* --- розпаковка ------------------------------------------------------- */
/*
 *  Той самий алгоритм, що перевірено на комп'ютері (verify_blob.py):
 *
 *    токен зі старшим розрядом  -> далі (t & 0x7FFF) окремих слів;
 *    токен без старшого розряду -> далі одне слово, повторити t разів.
 *
 *  Для опорного кадру слова записуються як є, для дельти застосовуються
 *  через «виключне або». Серія нулів у дельті означає «стільки пікселів
 *  не змінилося»: покажчик просто пересувається, пам'ять не чіпається.
 *  Саме на цьому й тримається швидкодія - у знятому матеріалі більшість
 *  кадру між сусідніми кадрами не змінюється.
 */
static void decode_frame(const unsigned short * src, int is_key) {
    unsigned short * dst = work;
    unsigned short * end = work + PIXELS;
    unsigned int     n;
    unsigned short   v;

    if (is_key) {
        while (dst < end) {
            unsigned short t = *src++;
            if (t & 0x8000) {
                n = t & 0x7FFF;
                while (n--)
                    *dst++ = *src++;
            } else {
                n = t;
                v = *src++;
                while (n--)
                    *dst++ = v;
            }
        }
    } else {
        while (dst < end) {
            unsigned short t = *src++;
            if (t & 0x8000) {
                n = t & 0x7FFF;
                while (n--)
                    *dst++ ^= *src++;
            } else {
                n = t;
                v = *src++;
                if (v) {
                    while (n--)
                        *dst++ ^= v;
                } else {
                    dst += n;        /* нічого не змінилося */
                }
            }
        }
    }
}

/* Копіювання робочого кадру в буфер пікселів. Рядок у буфері займає
   1024 байти, а корисних у ньому 640, тому копіювати одним шматком
   не можна - лише рядок за рядком. Копіюється словами по 32 розряди,
   тобто по два пікселі за раз. Раз на шістнадцять рядків черга звуку
   доливається, щоб вона не встигла спорожніти. */
static void blit(unsigned int buffer) {
    const unsigned int * s = (const unsigned int *)work;
    int y;

    for (y = 0; y < HEIGHT; y++) {
        volatile unsigned int * d =
            (unsigned int *)(buffer + (unsigned int)y * ROW_BYTES);
        int k = WIDTH / 2;           /* 160 слів на рядок */
        while (k--)
            *d++ = *s++;
        if ((y & 15) == 15)
            audio_pump();
    }
}

/* --- кнопки ----------------------------------------------------------- */
/* Повертає маску натиснутих кнопок після відпускання. Очікування
   відпускання потрібне, щоб одне натискання не спрацювало багато разів. */
static int read_keys(void) {
    volatile int * key = (int *)KEY_BASE;
    int v = *key;

    if (v == 0)
        return 0;
    while (*key)
        audio_pump();
    return v;
}

/* --- очікування блока -------------------------------------------------- */
/*
 *  Блок пише не програма, а зовнішній пристрій через міст JTAG, тому
 *  читати підпис треба через volatile: без нього компілятор має повне
 *  право прочитати його один раз перед циклом і зациклитися назавжди.
 *  Так воно й було - прогін програми на комп'ютері це показав, і в
 *  згенерованому для плати коді читання теж стояло перед циклом.
 *
 *  Чекати самого підпису на початку блока недостатньо: заливка йде від
 *  початку до кінця, тобто підпис приїздить першим, а дані ще ні.
 *  Тому пакувальник дублює підпис в останніх чотирьох байтах, і показ
 *  починається лише тоді, коли видно обидва.
 */
static int blob_ready(void) {
    volatile unsigned int * head = (unsigned int *)BLOB_BASE;
    volatile unsigned int * size = (unsigned int *)(BLOB_BASE + 28);
    volatile unsigned int * tail;
    unsigned int n;

    if (*head != BLOB_MAGIC)
        return 0;
    n = *size;
    if (n < 64 || n > BLOB_MAX || (n & 3))
        return 0;
    tail = (unsigned int *)(BLOB_BASE + n - 4);
    return *tail == BLOB_MAGIC;
}

/* ===================================================================== */
int main(void) {
    volatile unsigned int * ledr = (unsigned int *)LEDR_BASE;
    volatile int          * sw   = (int *)SW_BASE;

    const struct blob_header * hdr = (const struct blob_header *)BLOB_BASE;
    const struct frame_entry * index;
    const unsigned char      * blob = (const unsigned char *)BLOB_BASE;

    unsigned int front = FRONT_BUFFER;
    unsigned int back  = BACK_BUFFER;
    unsigned int ticks_per_frame;
    unsigned int mark;               /* мітка витримки часу кадру      */
    unsigned int work_start;         /* коли почалася робота над кадром */
    unsigned int frame = 0;
    int paused = 0;
    int missed = 0;
    int keys;
    int have_audio = 0;
    int spf = 0;
    unsigned int ablock = 0, aoff = 0;

    a_pos = a_count = 0;
    a_shift = 16;

    /* Поки блок не залито, чекаємо: так порядок дій у лабораторії стає
       вільним - можна спершу запустити програму, а вже потім залити
       ролик. Поки чекаємо, на індикаторах світиться "------". */
    while (!blob_ready()) {
        volatile unsigned int * hex30 = (unsigned int *)HEX3_HEX0_BASE;
        volatile unsigned int * hex54 = (unsigned int *)HEX5_HEX4_BASE;
        *hex30 = 0x40404040u;        /* самі середні сегменти */
        *hex54 = 0x00004040u;
        *ledr  = 0x200;              /* LEDR9 - чекаємо даних */
    }

    index = (const struct frame_entry *)(BLOB_BASE + hdr->index_off);
    ticks_per_frame = TIMER_HZ / (hdr->fps ? hdr->fps : 24);

    /* Звук беремо лише тоді, коли він є і в блоці, і в прошитій
       системі: та сама програма має працювати на обох конфігураціях. */
    if (hdr->version >= 2 && (hdr->flags & 1)
        && hdr->audio_spf > 0 && hdr->audio_spf <= MAX_SPF
        && audio_probe()) {
        have_audio = 1;
        spf    = (int)hdr->audio_spf;
        ablock = hdr->audio_block;
        aoff   = hdr->audio_off;
    }

    /* Обидва буфери одразу чорні, щоб на екрані не було сміття. */
    {
        int i;
        for (i = 0; i < PIXELS; i++)
            work[i] = 0;
        blit(front);
        blit(back);
    }

    pixel_set_back(back);
    pixel_swap_request();
    pixel_swap_wait();

    timer_start();
    mark = timer_now();
    *ledr = 0x1 | (have_audio ? 0x4 : 0);

    while (1) {
        keys = read_keys();

        if (keys & 0x1) {                   /* KEY0 - пауза й продовження */
            paused = !paused;
            mark = timer_now();             /* не наздоганяти час паузи   */
            if (paused && have_audio)
                audio_clear();
        }
        if (keys & 0x2) {                   /* KEY1 - з початку           */
            frame = 0;
            missed = 0;
            paused = 0;
            mark = timer_now();
            a_pos = a_count = 0;
            if (have_audio)
                audio_clear();
        }

        if (paused) {
            *ledr = 0x2 | (have_audio ? 0x4 : 0) | (missed ? 0x200 : 0);
            continue;
        }

        if (frame >= hdr->frames) {
            if (*sw & 0x1) {                /* SW0 - зациклювання         */
                frame = 0;
            } else {
                paused = 1;
                if (have_audio)
                    audio_clear();
                continue;
            }
        }

        /* --- звук цього кадру --- */
        if (have_audio) {
            const unsigned char * b = blob + aoff + (unsigned int)frame * ablock;
            /* гучність: SW[2:1] послаблюють вихід, якщо повний надто гучний */
            a_shift = 16 - ((*sw >> 1) & 0x3);
            adpcm_decode(b + 0, b + 8, spf, a_left);
            adpcm_decode(b + 4, b + 8 + spf / 2, spf, a_right);
            a_pos = 0;
            a_count = spf;
            audio_pump();               /* наповнити чергу перед роботою  */
        }

        /* --- кадр --- */
        work_start = timer_now();
        {
            unsigned int raw    = index[frame].size;
            int          is_key = (raw & 0x80000000u) ? 1 : 0;
            const unsigned short * data =
                (const unsigned short *)(BLOB_BASE + index[frame].offset);

            decode_frame(data, is_key);
            audio_pump();
            blit(back);
        }

        /* Кадр готовий - замовляємо обмін. Апаратура виконає його на
           найближчій кадровій синхронізації, поки ми витримуємо час. */
        pixel_set_back(back);
        pixel_swap_request();

        /* Чи вклалися розпаковка й копіювання в час кадру. Це саме
           перевірка: темп задає не вона, а витримка нижче. */
        if ((work_start - timer_now()) >= ticks_per_frame)
            missed = 1;

        if (have_audio) {
            /* Темп задає кодек: він забирає рівно spf відліків за
               1/24 секунди, і цикл стоїть рівно стільки, скільки
               потрібно, щоб їх віддати. Заразом це тримає звук і
               зображення разом - спільного лічильника, який міг би
               розійтися, тут просто немає. */
            while (a_pos < a_count)
                audio_pump();
            mark = timer_now();
        } else {
            /* Без звуку темп задає таймер. Наступна мітка відлічується
               від попередньої, а не від поточного моменту, тому дрібні
               коливання тривалості розпаковки не накопичуються. Таймер
               лічить униз, тому пройдений час - це різниця «мітка мінус
               поточне значення», порахована беззнаково. */
            if ((mark - timer_now()) >= ticks_per_frame) {
                missed = 1;
                mark = timer_now();  /* відстали - починаємо відлік заново */
            } else {
                while ((mark - timer_now()) < ticks_per_frame)
                    ;
                mark -= ticks_per_frame;
            }
        }

        pixel_swap_wait();

        {
            unsigned int t = front;
            front = back;
            back = t;
        }

        show_number(frame);
        *ledr = 0x1 | (have_audio ? 0x4 : 0) | (missed ? 0x200 : 0);
        frame++;
    }
}
