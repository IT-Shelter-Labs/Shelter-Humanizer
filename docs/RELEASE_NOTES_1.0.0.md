## Shelter Humanizer 1.0.0

Первый публичный выпуск: ИИ-редактура текста и проверка скрытых символов
в простом Windows-приложении. Русский — основной профиль правил.

### Начать

1. Скачать **Shelter-Humanizer-1.0.0-windows-x64.zip** ниже, распаковать весь архив.
2. Открыть **Shelter-Humanizer.exe** — Python не нужен.
3. **«Выбрать ИИ» → «Установить локальный ИИ»** → выбрать модель и подтвердить загрузку.
4. Вставить текст → **«Улучшить текст»** → прочитать результат → **«Копировать»**.

Основной вариант — **DeepSeek R1 0528 Qwen3 8B**, ≈5,2 ГБ. Компоненты Ollama
занимают ещё ≈1,4 ГБ; желательно 24 ГБ RAM или подходящая видеокарта.
Для менее мощного компьютера доступны модели Qwen. После первой загрузки
локальная редактура работает без интернета; собственный ИИ выключается с приложением.

### Возможности

- Переписывание целого текста или небольшие правки; тип текста, образец стиля и важные слова.
- Исходник и результат рядом, сравнение изменений, копирование, TXT и отчёт JSON.
- Очистка нежелательных скрытых символов после ИИ-генерации и вычитки.
- Светлая/тёмная темы, подсказки, горячие клавиши в русской/английской раскладках.
- Внешний ИИ-чат и OpenAI-compatible API как альтернативы локальной модели.
- CLI и отдельный portable skill для агентов.

[Русская инструкция](https://github.com/IT-Shelter-Labs/Shelter-Humanizer/blob/main/docs/QUICKSTART_RU.md) · [English README](https://github.com/IT-Shelter-Labs/Shelter-Humanizer/blob/main/README_EN.md) · [Статус проверок](https://github.com/IT-Shelter-Labs/Shelter-Humanizer/blob/main/docs/STATUS.md)

**Assets:** Windows ZIP — приложение; skill ZIP — отдельная инструкция для агента;
SHA256-файлы — контрольные суммы. Source code — исходники, без готового EXE.

Сборка не подписана сертификатом. Windows может показать SmartScreen.
Проверяйте источник и контрольную сумму. Модели не входят в ZIP, скачиваются
только после подтверждения. История текстов и API-ключи автоматически не записываются.
Ответы ИИ нужно вычитывать. Приложение не измеряет плагиат и не гарантирует
прохождение AI-детекторов. Интерфейс на русском; английская документация прилагается.

---

### English

First public release: a Russian-first Windows app for AI text editing and
hidden-character inspection. Download the Windows ZIP, extract it, and open
Shelter-Humanizer.exe. Python is not required. The installer offers local DeepSeek
R1 0528 Qwen3 8B and smaller Qwen alternatives; model downloads require confirmation.
An existing AI chat and OpenAI-compatible API can also be used.

Cleanup runs after AI editing. Source text, protected data, and valid Unicode
characters are preserved. Always review meaning; no plagiarism score or detector
passing guarantee is provided. The desktop UI is in Russian.
