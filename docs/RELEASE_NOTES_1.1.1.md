# Shelter Humanizer 1.1.1

Небольшое обновление: меню редактирования по правой кнопке мыши и более полезная
диагностика локального ИИ. Готовое Windows-приложение, Python не нужен.

## Скачать и обновить

1. В **Assets** скачайте **Shelter-Humanizer-1.1.1-windows-x64.zip**.
2. Закройте предыдущую версию, распакуйте весь ZIP в отдельную папку и запустите
   **Shelter-Humanizer.exe**.
3. Скачанные встроенные модели и сохранённый выбор используются из прежней папки
   данных. Повторная загрузка обычно не нужна.

## Что изменилось

- **Меню по ПКМ:** копирование, вставка, вырезание, удаление выделенного и
  выделение всего в исходнике, результате и полях настроек. В многострочных полях
  также есть отмена и повтор. Меню открывается с клавиатуры через **Shift+F10**.
  Выделение сохраняется; изменение полей только для чтения недоступно.
- **Понятнее ошибки Ollama:** известные сообщения о RAM/VRAM, GPU, файле модели,
  архитектуре или завершении процесса получают конкретные подсказки. Если причина
  неизвестна, приложение сообщает об этом. HTTP 500 не считается доказательством нехватки памяти.
- **Опционально только процессор:** **«Выбрать ИИ → Настроить подключение →
  Только процессор»** запрашивает `num_gpu=0` у Ollama. Может помочь при сбое GPU
  или драйвера, но работает медленнее и не устраняет нехватку RAM. Действует в текущем
  запуске; запрос повторяется вручную. Модель и системные настройки не меняются.
- **Диагностика не теряет ошибку:** после проверки подключения последняя ошибка
  генерации остаётся в `last_failure`. Успешный новый запрос не выдаёт прежнюю
  категорию за новый сбой. Сырой ответ движка, текст, ключи и личные пути не экспортируются.
- **Настройки подключения:** при изменении опций сохраняются управление собственной
  Ollama и прежний предел времени генерации. CPU-only учитывается при продолжении частей.

Qwen 3.5 4B, режим небольших правок, проверенная инструкция, защита данных
и очистка Unicode после генерации сохранены.

## Проверки и ограничения

212 тестов прошли локально на Windows / Python 3.12.14 без пропусков. Кандидат
также прошёл проверку готового EXE и ручную проверку владельцем. Ошибки движка
проверяются локальным имитатором; CPU-only проверяется по параметру запроса.
Причина конкретного HTTP 500 у подписчика без его диагностики не установлена.
Устранение всех ошибок GPU, модели или памяти не обещается.

Выпуск собирается из коммита, прошедшего GitHub Actions на Windows/Linux и
Python 3.11/3.13. В Windows ZIP есть `BUILD_INFO.json` и `SELFTEST.json`.

[Русская инструкция](https://github.com/IT-Shelter-Labs/Shelter-Humanizer/blob/v1.1.1/docs/QUICKSTART_RU.md) ·
[Настройки и свои модели](https://github.com/IT-Shelter-Labs/Shelter-Humanizer/blob/v1.1.1/docs/ADVANCED_SETTINGS_RU.md) ·
[English README](https://github.com/IT-Shelter-Labs/Shelter-Humanizer/blob/v1.1.1/README_EN.md) ·
[Решение проблем](https://github.com/IT-Shelter-Labs/Shelter-Humanizer/blob/v1.1.1/docs/TROUBLESHOOTING_RU.md) ·
[Telegram IT Shelter](https://t.me/+txLM4MYbMh9lZDli)

**Assets:** Windows ZIP содержит приложение, инструкции и лицензии; skill ZIP
содержит отдельный скилл для агента. `SHA256SUMS.txt` и `SHA256SUMS-skill.txt`
содержат контрольные суммы. Автоматический **Source code (zip)** содержит исходники без EXE.

Сборка не подписана сертификатом. Проверяйте источник и SHA256; отключать защиту
Windows не требуется. Веса моделей в ZIP не входят. История текстов и API-ключи
автоматически не сохраняются. Ответы ИИ нужно вычитывать; гарантий обхода детекторов нет.

## English

Download **Shelter-Humanizer-1.1.1-windows-x64.zip**, close the old app, extract
into a separate folder and run the EXE. Python is not required. Managed models
and explicit saved selections are reused. The interface is in Russian; English
instructions are included.

This patch adds right-click / Shift+F10 editing menus, safer Ollama failure
messages, optional per-request CPU-only execution and last-failure diagnostics
that survive a connection check. Unknown HTTP 500 causes remain explicitly unknown.
There are no automatic retries or model switches. Hardware compatibility varies;
the executable is unsigned. Model weights are not bundled.
