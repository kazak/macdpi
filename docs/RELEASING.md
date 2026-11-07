# Выпуск на GitHub

## Подготовленный проект

В проект не включены сетевые настройки пользователя, ключ управления, логи,
состояние режима, пароли или персональные пути. `vendor/` содержит исходники движка
и его лицензию; скомпилированный движок собирается заново.

До первого публичного выпуска:

1. Проверьте YouTube, веб-чат и вложения Discord в реальной сети. Голос не входит в поддержку.
2. Установите сборку на чистом Mac с macOS 13+ и Command Line Tools; проверьте первую
   настройку, восстановление прежнего PAC и запуск после входа в учётную запись.
3. Для подписанного релиза, проверяемого Gatekeeper, подготовьте Developer ID
   Application, подпись и notarization. Эти данные в проект не включены.

## Сборка

```sh
python3 scripts/build.py
TPWS_BIN="$PWD/dist/Tube Switch.app/Contents/Resources/tpws" python3 -m unittest discover -s tests -v
```

Для подписи сертификатом из Keychain:

```sh
CODESIGN_IDENTITY='Developer ID Application: YOUR NAME (TEAMID)' python3 scripts/build.py
```

Для отправки на notarization используйте заранее настроенный профиль Keychain:

```sh
CODESIGN_IDENTITY='Developer ID Application: YOUR NAME (TEAMID)' \
  python3 scripts/build.py --notarize-profile YOUR_NOTARY_PROFILE
```

Скрипт отправляет ZIP через `notarytool`, прикрепляет ticket к приложению,
пересобирает ZIP и только после этого вычисляет SHA-256. Секреты не добавляйте в git.

## Черновик релиза

После создания своего репозитория и отправки проекта:

```sh
git tag -a v1.1.0 -m 'Tube Switch 1.1.0'
git push origin v1.1.0
```

Workflow проверит совпадение тега с `VERSION`, соберёт приложение, выполнит тесты
и создаст **draft release** с ZIP приложения, архивом исходников и контрольными суммами.
По умолчанию workflow не использует Developer ID и не выполняет notarization.

Перед Publish release загрузите подготовленную подписанную сборку и обновлённые
контрольные суммы либо обозначьте ограничения ad-hoc-сборки в заметках релиза.

Перед публикацией также выполните `python3 scripts/audit.py --artifacts dist/*.zip`
и сканирование репозитория через Gitleaks. Локальный ключ управления создаётся
на компьютере пользователя и не должен входить ни в git, ни в архив приложения.

Публикацию также запускает push в `main`, который изменяет `VERSION` или релизный
workflow и содержит явную отметку `[release]` в сообщении коммита. Остальные push
в `main` не публикуют релиз. В этом режиме тег берётся из `VERSION`.
