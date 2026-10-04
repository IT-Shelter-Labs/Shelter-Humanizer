# Публикация Shelter Humanizer

Исходники публикуются в `IT-Shelter-Labs/Shelter-Humanizer`.
Готовые бинарники добавляются в GitHub Release, а не в историю Git.

## Подготовить выпуск

1. Согласовать версию в `pyproject.toml`, `__init__.py`, START_HERE и документации.
2. Запустить тесты, Ruff, сборку и self-test распакованного EXE.
3. Обновить `docs/STATUS.md` по фактическим результатам, не переносить старые цифры.
4. Собрать `python tools/build_release.py` и `python tools/package_skill.py`.
5. Убедиться, что `dist/`, `build/`, `local-evaluation/`, `.env` и ключи не попадут в Git.
6. Отправить исходники и дождаться всех jobs GitHub Actions. При ошибке исправить её до анонса.

## Release 1.0.0 через сайт

Releases → Draft a new release. Тег `v1.0.0`, Target `main`.
Заголовок: `Shelter Humanizer 1.0.0`.
Описание: содержимое [RELEASE_NOTES_1.0.0.md](RELEASE_NOTES_1.0.0.md).
Приложить:

- `dist/windows/Shelter-Humanizer-1.0.0-windows-x64.zip`;
- `dist/windows/SHA256SUMS.txt`;
- `dist/skill/Shelter-Humanizer-1.0.0-skill.zip` (дополнительный способ использования);
- `dist/skill/SHA256SUMS-skill.txt`.

Автоматический Source code (zip) не заменяет архив с EXE. Сначала прикрепите все
файлы и проверьте описание, затем Publish release. Для готового 1.0.0 не отмечайте
pre-release; выберите Set as latest release. Если приёмка выявила проблему,
исправьте её до стабильного выпуска или явно выпускайте отдельный prerelease.

## Через GitHub CLI, если он установлен

Из корня репозитория после успешного CI и отправки тега:

```powershell
gh auth login
gh release create v1.0.0 --repo IT-Shelter-Labs/Shelter-Humanizer --verify-tag --title "Shelter Humanizer 1.0.0" --notes-file docs/RELEASE_NOTES_1.0.0.md --draft dist/windows/Shelter-Humanizer-1.0.0-windows-x64.zip dist/windows/SHA256SUMS.txt dist/skill/Shelter-Humanizer-1.0.0-skill.zip dist/skill/SHA256SUMS-skill.txt
gh release edit v1.0.0 --repo IT-Shelter-Labs/Shelter-Humanizer --draft=false --latest
```

Последняя команда публикует подготовленный черновик; сначала проверьте его на сайте.
GitHub CLI необязателен, все действия доступны через браузер.

## Оформление репозитория

Описание About: `AI text editing and hidden Unicode cleanup. Russian-first Windows app with local Qwen, DeepSeek, Ollama, API and portable skills.`

Topics: `humanizer`, `russian-language`, `text-editing`, `unicode`, `qwen`, `deepseek`,
`ollama`, `python`, `tkinter`, `agent-skills`, `open-source`.

Включить Issues и Private vulnerability reporting, выбрать MIT, добавить ссылку
на Telegram в About. После первого успешного CI можно создать ruleset для main:
запрет force push/удаления и обязательные проверки. Для единственного сопровождающего
не включайте требование чужого approval, если оно блокирует собственные PR.
Закрепите репозиторий на странице организации.

## После публикации

Скачайте ZIP из публичного Release, проверьте SHA256 и первый запуск из новой папки.
Откройте ссылки из обоих README без авторизации. Только затем используйте
`releases/latest` в посте и видео.

Для демо показывайте фактический ответ указанной версии и модели. Не выдавайте
ручную правку или эхо-тест за генерацию. Не обещайте процент уникальности или
прохождение детекторов. API-ключ и личные тексты не должны попадать в кадр.
