# 🔎 Userbot: охотник за юзами

Telegram-бот, который генерирует красивые юзернеймы и проверяет, свободны ли они.

## Как это работает
Bot API не умеет проверять, свободен ли юзернейм. Поэтому бот проверяет через **ваш обычный аккаунт**
(Telethon, метод `account.checkUsername`), это тот же запрос, который шлёт приложение, когда вы вводите
новый юз в настройках. Аккаунт ничего не меняет, только спрашивает. Ответ бывает такой:

- ✅ свободен, можно занять бесплатно
- ❌ занят
- 💎 продаётся на Fragment
- 🚫 запрещён или зарезервирован

## Режимы генерации
| режим | пример |
|---|---|
| `word`: словарные слова (`userhunter/words.txt`) | `zenith`, `quasar` |
| `pron`: читаемые 5–6 букв | `kamor`, `lumina` |
| `pattern`: повторы | `ababa`, `xxkkx` |
| `palindrome` | `vezev` |
| `affix`: слово + приставка/суффикс | `thepixel`, `pixelhq` |
| `digits`: слово + цифры | `nova777` |
| `short`: случайные 5 букв | `kotaz` |

Плюс поиск по маске `/mask` (`c` согласная, `v` гласная, `l` буква, `d` цифра, `A`/`B`… одинаковые буквы)
и вариации своего слова через `/like`.

## Установка
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполните BOT_TOKEN, API_ID, API_HASH, ADMIN_IDS
python -m userhunter.login   # один раз: вход в аккаунт по номеру и коду
python -m userhunter         # запуск бота
```
- `BOT_TOKEN` берётся у [@BotFather](https://t.me/BotFather)
- `API_ID` / `API_HASH` берутся на https://my.telegram.org → API development tools
- `ADMIN_IDS` — ваш Telegram ID (подскажет [@userinfobot](https://t.me/userinfobot)). Пользоваться ботом смогут только эти люди.

## Команды
```
/hunt pron 10     найти 10 свободных юзов в режиме pron
/mask cvcvc 5     поиск по маске
/like pixel       вариации слова
/check durov abc  проверить конкретные юзы
/found            лучшие найденные (с оценкой «крутости»)
/stats  /stop  /help
```

## Важно
- Telegram ограничивает частоту проверок. При FloodWait бот сам подождёт нужное время. Не ставьте `CHECK_DELAY` меньше 2–3 секунд.
- Короткие словарные юзы почти все заняты или висят на Fragment, так что режимы `pron`, `affix` и `/mask` находят больше.
- Файл `*.session` даёт полный доступ к аккаунту: никому его не передавайте (он уже в `.gitignore`). Можно завести отдельный аккаунт специально для проверок.
