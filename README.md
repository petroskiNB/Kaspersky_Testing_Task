# CPU-инференс LLM в Kubernetes

Развёртывание `Qwen/Qwen2.5-1.5B-Instruct` через vLLM 0.27.0 в Minikube.
Инференс выполняется на CPU: BF16, eager, контекст 2048 токенов,
до двух одновременно обрабатываемых последовательностей.

## Основные требования

| Требование | Реализация |
|---|---|
| Собственный Dockerfile, запуск без root | Сервер `llm-lab:v0.3`, пользователь UID/GID 10001 |
| Закреплённые зависимости | Базовые образы по digest, точные версии прямых и транзитивных пакетов; проверка окружения и `pip check` при сборке |
| Логирование без вопросов и ответов | ASGI middleware выводит JSON: ID, метод, известный путь, статус и длительность. Стандартные Python-логи подавлены |
| Постоянное хранение модели | PVC 10 GiB; загрузчик записывает файлы, сервер читает их в offline-режиме |
| Повторный запуск без Hugging Face | Проверка ревизии и SHA-256 файлов; при готовой модели — `model_cache_hit` без сетевых запросов |
| Secrets1 | HF-токен доступен только загрузчику, отдельный Bearer-токен защищает API |
| ConfigMap | Параметры vLLM и окружения меняются без пересборки образа; требуется перезапуск Pod |

Схема: **Hugging Face → init-контейнер → PVC → сервер vLLM → Service → клиент**.

## Запуск

Нужны Docker Desktop с WSL2/Linux-контейнерами, Minikube и PowerShell.
Для тестов — Python 3.12+ и `curl.exe`. Команды выполняются из корня проекта.
У Minikube должен быть StorageClass по умолчанию (`standard` в проверенной среде).

```powershell
minikube start --profile=llm-lab --driver=docker --container-runtime=containerd --cpus=4 --memory=12288

docker build -t llm-lab:v0.3 .
docker build -f downloader/Dockerfile -t llm-downloader:v0.2 .

minikube --profile=llm-lab image load llm-lab:v0.3
minikube --profile=llm-lab image load llm-downloader:v0.2

powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Setup-Kubernetes.ps1
minikube --profile=llm-lab kubectl -- apply -k ./k8s
minikube --profile=llm-lab kubectl -- get pods -n llm-lab
```

Скрипт настройки сохраняет полный SHA модели, запрашивает HF Read-токен скрытым
вводом и генерирует API-токен. Существующие значения сохраняются при повторном
запуске. Токены не записываются в файлы проекта. Base64 в Secret не является шифрованием.

Первая загрузка может занять десятки минут. Просмотр её состояния:

```powershell
minikube --profile=llm-lab kubectl -- logs -f deployment/vllm -c download-model -n llm-lab
```

После `READY 1/1` запустить в отдельном терминале:

```powershell
minikube --profile=llm-lab kubectl -- port-forward service/vllm 8001:8000 -n llm-lab
```

API доступен на `http://127.0.0.1:8001`: `/health` без токена,
`/v1/chat/completions` с `Authorization: Bearer <API-токен>`.
После пересоздания Pod перезапускайте `port-forward`.
PVC переживает замену Pod, но удаление PVC/кластера может удалить модель.

## Проверки

```powershell
# Локальные тесты без модели
python -B -m unittest discover -s tests -v

# Авторизация и отсутствие утечек в логах Kubernetes-приложения
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Test-LoggingPrivacy.ps1 -Kubernetes -BaseUrl http://127.0.0.1:8001
```

Результаты: **14 локальных тестов прошли**. В Kubernetes прошли пять сценариев:
обычный запрос и streaming — HTTP 200; ошибка валидации — 400;
без токена и с неверным токеном — 401. Метаданные присутствуют,
маркеры запросов, полученные ответы и токены в логах не обнаружены.
Это проверка указанных сценариев, а не гарантия для всех возможных сбоев.

Также проверены UID/GID 10001, повторное использование PVC и изменение контекста
`2048 → 1024 → 2048` без изменения digest образа.

## Дополнительные задания

### 1. Kustomize

`k8s/kustomization.yaml` объединяет Namespace, PVC, ConfigMap, Deployment и Service.
Все манифесты применяются одной командой `kubectl apply -k ./k8s`.
Secrets создаются отдельно скриптом настройки.

### 2. Отдельный init-контейнер

`downloader/Dockerfile` — лёгкий Python-образ без vLLM, с закреплёнными
зависимостями и пользователем 10001. Он проверяет/скачивает модель и завершается;
сервер запускается только после успешной подготовки. Неполная загрузка
не получает маркер готовности. Повторный запуск подтверждён событием `model_cache_hit`.

### 3. Нагрузочные тесты и подбор настроек

Среда: AMD Ryzen 7 8845HS, лимиты сервера **4 CPU / 10 GiB RAM**, KV-кеш 1 GiB.
Короткий вход — около 71 токена, длинный — около 1192; выход ограничен 64 токенами.
По 10 запросов на сценарий, один и два одновременных клиента, прогрев исключён.
Сравнивались `max-num-seqs=1` и `2`; digest сервера и ресурсы совпадают.
**Всего 80 измеряемых запросов, ошибок нет.**

| Вход | Клиентов | TTFT p50, с: 1 → 2 | Полное время p50, с: 1 → 2 | Общая скорость, выходных токенов/с: 1 → 2 |
|---|---:|---:|---:|---:|
| Короткий | 1 | 0,842 → 0,825 | 15,390 → 13,820 | 4,16 → 4,62 |
| Короткий | 2 | 15,437 → 1,282 | 29,730 → 14,784 | 4,32 → 8,66 |
| Длинный | 1 | 10,353 → 10,152 | 24,392 → 23,551 | 2,62 → 2,70 |
| Длинный | 2 | 34,145 → 15,633 | 47,732 → 34,530 | 2,68 → 3,70 |

**Итог: оставлено `max-num-seqs=2`.** При двух клиентах пропускная способность
выросла на 100% для коротких запросов и на 38% для длинных. Медиана полного
времени уменьшилась на 50% и 28% соответственно. Изменилось число одновременно
обрабатываемых последовательностей, а не количество CPU.

Повторить замер текущей конфигурации:

```powershell
python -B .\scripts\benchmark.py --base-url http://127.0.0.1:8001 --requests 10 --concurrency 1 2 --max-tokens 64 --label seqs2 --output reports/seqs2-repeat.json
```

Для сравнения конфигураций используйте `Test-ConfigMap.ps1` с параметрами
`-MaxModelLen 2048 -MaxNumSeqs 1` или `-MaxModelLen 2048 -MaxNumSeqs 2`,
затем перезапускайте `port-forward`. Скрипт проверяет готовность,
неизменность образа и повторное использование модели.

TTFT измеряется до первого непустого SSE-фрагмента; TPOT приближён по времени
первого/последнего фрагмента и количеству токенов. Задержки включают `port-forward`.
Выборка небольшая: p95 предварительный, отдельные межтокенные интервалы не измерялись.

Отчёты: [baseline](reports/baseline.json), [seqs2](reports/seqs2.json),
[подробное сравнение](reports/comparison.md).
Разбор Python-кода: [docs/python-files.md](docs/python-files.md).
