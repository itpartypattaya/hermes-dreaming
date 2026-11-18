"""Finding 18 of the code review of 2026-11-17: secrets with no separator before their value.

The quarantine took a password or a PIN only with ":", "=" or a dash between the word and the value, and no rule
looked for a payment card. "пароль wifi дома Slon2024xx", "PIN карты 4821" and a card number with its CVV went through
— into the weekly extraction's hand-over (dream-extract-precheck passes on what classify_unsafe lets through), from
there into a fact, and the provider's own screen, the same one, stores it. A live install had 34 numbers in its
history that pass the card checksum and 23 messages with a PIN or a password the quarantine let through.

Requirements:
- a payment card number (16 to 19 digits starting 2–6, or an American Express 15, whole or in groups, passing the
  Luhn checksum) is a secret;
- a PIN, CVV or CVC with a number of 3 to 8 digits a few words after it is a secret;
- a password word with a password-like value a few words after it (letters and digits, a symbol, or six digits and
  more) is a secret;
- and none of these is a secret: a phone number, a Telegram chat id, a date or a time, an order number that fails
  the checksum, a year a PIN was changed in, a PIN length, a password word with only words after it.
"""

import unittest

from test_dream import dream


def _card(prefix):
    """*prefix* with the Luhn check digit appended."""
    digits = [int(c) for c in prefix][::-1]
    total = 0
    for i, d in enumerate(digits):
        if i % 2 == 0:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
    return prefix + str((10 - total % 10) % 10)


VISA = _card("427616001234567")          # 16
MIR = _card("220012345678901")           # 16
AMEX = _card("37828224631000")           # 15
LONG = _card("627654321098765432")       # 19
BAD = VISA[:-1] + str((int(VISA[-1]) + 1) % 10)   # one digit off: fails the checksum


def _groups(number, sizes=(4, 4, 4, 4)):
    out, i = [], 0
    for n in sizes:
        out.append(number[i:i + n])
        i += n
    return " ".join(out + ([number[i:]] if number[i:] else []))


class SecretsWithoutASeparator(unittest.TestCase):
    def test_a_card_number_is_a_secret(self):
        for text in (f"Номер карты {_groups(VISA)}, cvv 123", f"карта Мир {MIR} на имя Нины",
                     f"Card {_groups(VISA).replace(' ', '-')} expires 09/28", f"Amex {AMEX}",
                     f"номер карты для перевода {LONG}", f"переведи на {_groups(MIR)} до пятницы"):
            with self.subTest(text=text):
                self.assertEqual(dream.classify_unsafe(text), "secret")

    def test_a_pin_or_cvv_with_its_number_is_a_secret(self):
        for text in ("PIN карты 4821", "пин-код от домофона 2580", "ПИН 0417, не забудь", "cvv 123",
                     "CVC2 на обороте: 907", "код безопасности 345 на обороте", "pin code 99021"):
            with self.subTest(text=text):
                self.assertEqual(dream.classify_unsafe(text), "secret")

    def test_a_password_a_few_words_after_its_word_is_a_secret(self):
        for text in ("пароль wifi дома Slon2024xx", "Пароль от почты Gmail2024!", "пароль на ноутбуке 84736251",
                     "password for the router Tiger#77", "пароли от сейфа у мамы kot2024"):
            with self.subTest(text=text):
                self.assertEqual(dream.classify_unsafe(text), "secret")


class NotSecrets(unittest.TestCase):
    def test_numbers_that_are_no_card(self):
        for text in ("Телефон Нины +7 912 345-67-89", "звони на +66 81 234 5678", "чат -1001234567890 ветка 43",
                     f"номер заказа {BAD}", f"трек {_groups(BAD)}", "в 2026-11-18 12:00:00 упал gateway",
                     "таймстемп 1763380800000 из лога", "ИНН 7707083893", "счёт 40817810099910004312",
                     "сумма 1 234 567 890 рублей",
                     # ids in addresses and glued to letters, as a live install's history has them (2026-11-18)
                     f"https://x.com/someone/status/{LONG} смешно", f"https://www.facebook.com/groups/{VISA}/?__tn__=R",
                     f'id: "{VISA}abcd" (ok)'):
            with self.subTest(text=text):
                self.assertIsNone(dream.classify_unsafe(text))

    def test_a_pin_word_without_its_number(self):
        # ("PIN 2025 года" is not here: the provider's own screen holds a number right after "PIN", and the dream
        # never lets through what the provider holds.)
        for text in ("пин-код из 4 цифр", "сменил PIN в 2024 году", "CVV на обороте карты", "ввёл пин 3 раза подряд",
                     "pin the post to the top"):
            with self.subTest(text=text):
                self.assertIsNone(dream.classify_unsafe(text))

    def test_a_password_word_with_words_after_it(self):
        for text in ("Пароль от роутера на наклейке снизу", "пароль сменили 18.11.2026", "пароль из 12 символов",
                     "забыла пароль от почты, восстановила через телефон", "password policy requires twelve characters",
                     "пароль у Нины в заметках"):
            with self.subTest(text=text):
                self.assertIsNone(dream.classify_unsafe(text))


if __name__ == "__main__":
    unittest.main()
