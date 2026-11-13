# -*- coding: utf-8 -*-
"""Куди готується ролик: плата DE1-SoC (система ЛР6) або CPUlator."""
from dataclasses import dataclass

WIDTH, HEIGHT, FPS = 320, 240, 24
KEYINT = 48                          # опорний кадр раз на 2 с
BLOB_BASE = 0xC0100000               # тут програвач шукає ролик (SDRAM за 1 МБ від початку)
BLOB_MAX = 0x03F00000                # до кінця SDRAM, 63 МіБ
CPULATOR_PLAIN_LIMIT = 2_300_000     # більший файл звичайний CPUlator не відтворить

# Якість: поріг мертвої зони. 0 — без утрат, більше — менший файл.
QUALITY = {
    'best': (1, 'найкраща (поріг 1)'),
    'good': (2, 'висока (поріг 2)'),
    'compact': (4, 'компактна (поріг 4)'),
}


@dataclass(frozen=True)
class Target:
    key: str
    title: str
    audio_rate: int      # частота, на яку перераховується доріжка
    spf: int             # відліків звуку на кадр (audio_rate / 24)
    header_rate: int     # частота, записана в заголовок
    quality: str         # якість за замовчуванням
    board: bool


TARGETS = {
    # Звуковий порт CPUlator працює на 8 кГц. 334 відліки на кадр — 8016 Гц, тож
    # звук грає на 0,2 % повільніше, а з ним і зображення: програвач для CPUlator
    # бере темп від годинника, прив'язаного до черги звуку.
    'cpulator': Target('cpulator', 'CPUlator (звук 8 кГц)', 8016, 334, 8000, 'good', False),
    # На платі кодек системи ЛР6 налаштовано на 48 кГц: рівно 2000 відліків на кадр.
    'board': Target('board', 'Плата DE1-SoC, система ЛР6 (звук 48 кГц)', 48000, 2000, 48000, 'compact', True),
}
