# Сторонние компоненты

## tpws / zapret

- Автор: bol-van и участники проекта.
- Репозиторий: https://github.com/bol-van/zapret
- Зафиксированный commit: `73ee2aa04e6e94922e7dc07554a1c3a456dc3201`.
- Лицензия проекта: MIT, исходный текст сохранён в `vendor/ZAPRET-LICENSE.txt`.
- Исходники tpws, включая вложенные уведомления epoll-shim, находятся в `vendor/tpws-source.tar.gz`.
- Контрольная сумма архива зафиксирована в `vendor/manifest.json` и проверяется перед сборкой.

Доменные профили составлены из минимальных групп доменов сервисов. При проверке использованы
списки проектов [v2fly/domain-list-community](https://github.com/v2fly/domain-list-community/tree/master/data)
и [Flowseal/zapret-discord-youtube](https://github.com/Flowseal/zapret-discord-youtube).
Общие домены Cloudflare/CloudFront целиком в профили не включены.

Tube Switch не связан с YouTube, Discord, Twitch или SoundCloud. Названия сервисов используются
для обозначения профилей подключения.
