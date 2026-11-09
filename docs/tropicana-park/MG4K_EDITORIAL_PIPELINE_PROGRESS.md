# MG 4K / «Тропикана-парк» — отчет о реализации

**Дата:** 09.11.2026  
**Целевое действие:** загрузить новое фото, автоматически выполнить нейтральный технический мастер **без HDR**, проверить и утвердить, скачать **реальный 16-битный TIFF**, перенести его в MG 4K с HARD-локерами, после чего **только вручную** разрешить художественную генерацию.  
**Этап:** разработка и CI. **Оценка прогресса: 85%** — код и автономные тесты, не факт продакшн-релиза.  
**Контур:** MARINS OS · isolated Cloudflare R2 / MG4K R2 · GitHub PR #45 · Issue #44.  
**Архив:** DSC09818, V9, V10, Editorial 01 не перезаписывать.

## 1. Фактически реализовано в PR #45

| Функция | Реализация | Гейт |
| --- | --- | --- |
| Загрузка изображения | JPEG/PNG/WebP ≤8 МБ, SHA-256, новый UUID, отдельный R2 inbox и job record | Проверка типа, размера, доступа |
| Автоматический Tech Master | Cloudflare Workflow `mg4k-editorial-tech`, отдельный offline Container `editorial-tech-v1`; Python OpenCV + tifffile | Нет доступа к интернету/OpenRouter, не запускается платный MG4K runner |
| Прогресс | Persisted `processing-state.json` из реальных технических этапов, polling UI 5 сек, явные ошибки | Без синтетических процентов; хранение после refresh |
| Tech QA | Source SHA, 16-bit TIFF, исходная глубина сигнала, HDR absent, нативный R2 SHA-256 | Fail-closed до подтверждения данных |
| Просмотр | Полноразмерное 8-битное JPEG-представление TIFF с кнопкой 100% | Не называть JPEG 16-битным TIFF |
| Human QA | Четыре серверно проверяемых признака, визуальные чекбоксы и решение APPROVED/REJECTED | Одноразовая атомарная фиксация QA по SHA |
| Скачивание | Авторизованный stream настоящего `master16.tif` | Только после human APPROVED |
| Передача MG4K | SHA-verified copy из `TROPICANA_EDITORIAL_R2` в `TEMP_BUFFER_R7`, `handoff.json` | Только после human APPROVED, idempotent |
| Художественный контур | Кнопка «Добавить в художественный MG 4K» подставляет утверждённое JPEG-представление и локеры | Пользователь отдельно нажимает «Обработать» и проходит биллинговый гейт |
| Server-side enforcement | Backend проверяет bearer, approved TIFF SHA, preview SHA, manifest, hard locks | Клиент не может ослабить HARD-локеры в заявке на генерацию |

## 2. Архитектура

```
UPLOAD (isolated R2 + SHA256)
  -> Cloudflare Workflow (durable orchestration)
    -> Offline Tech Container (Python, no Internet, no HDR)
      -> 16-bit TIFF + browser preview + technical QA proof
    -> R2 native SHA256 verification
  -> HUMAN VISUAL QA (APPROVE / REJECT)
    -> Download full TIFF
    -> MG4K R2 copy + immutable lock manifest
      -> MG4K interface: imported approved proxy + HARD locks
        -> explicit user's paid GENERATIVE action + existing billing guard
        -> human postgeneration QA
```

**Примечание о точности:** если загрузить 8-битный JPEG, результат будет 16-битным TIFF-контейнером, но источник не приобретёт дополнительную RAW-точность. Новые RAW/ARW пока не поддерживаются входом общего автоматического процессора. Для новых фотографий процессор сохраняет исходную геометрию (identity); **автоматическая оптическая/перспективная коррекция для произвольного кадра не реализована**. Визуальная проверка геометрии обязательна. Историческая DSC09818 V10 RAW-проявка не изменена.

## 3. Проверки

- `tests/editorial-raster-tech.py`: 8/16bit TIFF, pixel preservation, NO HDR, mismatch/alpha rejection.
- `tests/editorial-tech-runtime.py`: synthetic PNG → private Container HTTP → TIFF, preview, qa, idempotence.
- `tests/editorial-jobs.test.mjs`: status, auth, independent QA, TIFF download, import, SHA lock enforcement, duplicate retries and negative cases.
- GitHub Actions `Editorial Photo CI`: JS/Python and code/build contract.
- `4K Upscaler Regression Guard`: preservation of existing main container and prior pipeline.
- `scripts/editorial_cloud_smoke.py`: post-deploy real API upload→Workflow→QA_PENDING. **Never auto-approve or spend credits.**

## 4. Релизный критерий

**Нельзя называть релиз завершённым**, пока не проверены:
1. Успешные оба GitHub CI на финальном commit.
2. GitHub Deploy MG4K Cloudflare Production — SUCCESS.
3. Публичный технический smoke — upload creates Workflow and ends at AWAITING_HUMAN_QA with real 16-bit TIFF; download/handoff blocked before human approval.
4. Реальная фотография пользователя: визуальный QA, скачивание TIFF, сверка SHA, импорт MG4K и проверка HARD-локеров; платный художественный запуск только по отдельному явному действию.
5. Сохранность исторических V9, V10, Editorial 01.

**Если какой-либо гейт не пройден:** оставить PR draft/hold, production не считать готовым. При необходимости откатить релиз.
