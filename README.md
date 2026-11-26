# Aidy

Голосовой ассистент для Windows: интерфейс на WPF (.NET 8) и ядро на Python —
распознавание речи Whisper, Voice ID, AI-планировщик на Google Gemini, агент
для управления Windows и локальный синтез голоса Kokoro. Голосовые команды и
ответы Aidy — на английском.

## Требования

- Windows x64
- Python 3.10–3.12 x64
- .NET SDK с поддержкой .NET 8 WPF

## Установка

```
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

`setup.ps1` ставит зависимости из `requirements-lock.txt`, проверяет их через
`pip check`, готовит модели Whisper, Voice ID и Kokoro и собирает приложение.
Если модели уже скачаны, добавьте `-SkipModelDownload`.

Ключи Google AI вводятся скрыто через `Configure-AidyCredentials.ps1` и
хранятся в `%LOCALAPPDATA%\Aidy\`, не в репозитории. Пример настроек AI —
`ai.example.json`.

## Запуск

```
dotnet build .\WpfApp1.csproj -c Release --nologo
powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-Aidy.ps1
```

`Start-Aidy.ps1` запускает готовую сборку Release и не пересобирает её: после
изменения исходников (включая Python) сначала выполните сборку.
Ярлык на рабочем столе создаёт `scripts/install-desktop.ps1`.

Режимы: `Set-AidyInterpretation.ps1 -Mode original|api` (локальный
интерпретатор или Google AI), `Set-AidyAgent.ps1 -State on|off`.

## Тесты

```
powershell -NoProfile -ExecutionPolicy Bypass -File .\Run-AidyTests.ps1
```

Скрипт запускает unit-тесты в режиме `original`, чтобы они не зависели от
живых облачных настроек. Скрипты в `scripts/` — отдельные проверки: часть из
них делает реальные запросы к API или меняет окна, файлы и яркость, поэтому
читайте скрипт перед запуском.

## Структура

| Путь | Что внутри |
| --- | --- |
| `App.xaml`, `Views/`, `ViewModels/`, `Resources/` | Интерфейс WPF: окно, страницы, орб, док-панель |
| `Services/PythonBridge.cs` | Запуск Python-ядра и обмен строками через stdin/stdout |
| `PythonCore/main.py` | Точка входа Python |
| `PythonCore/aidy/assistant.py` | Главный оркестратор: активация, команды, диалоги, Voice ID |
| `PythonCore/aidy/ai_*.py` | AI-слой: провайдер, планировщик, инструменты, исполнители |
| `PythonCore/aidy/voice.py`, `voice_style.py`, `Assets/voice_jarvis/` | Голос Aidy и готовые фразы |
| `PythonCore/aidy/voice_auth.py` | Voice ID: профили и роли Admin/User/Guest |
| `PythonCore/aidy/tests/` | Тесты |
| `Api/` | Локальный intent-сервис для режима `original` |
