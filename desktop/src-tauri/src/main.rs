//! Панель провайдера: окно в меню-баре поверх работающего агента.
//!
//! Приложение НЕ считает и не берёт задачи. Этим занимается агент, который
//! работает отдельно и под другим пользователем: панель, упавшая или закрытая,
//! не должна останавливать узел, а узел не должен зависеть от того, вошёл ли
//! кто-то в систему.
//!
//! Связь между ними — файл состояния, который агент переписывает раз в
//! несколько секунд. Не сокет и не порт: сокет пришлось бы охранять, порт —
//! тем более, а читать файл может кто угодно, кому его дали прочитать.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};

/// Где агент оставляет состояние. Системный путь, а не домашний каталог:
/// агент — служебный демон, и его данные переживают смену пользователя.
fn state_path() -> PathBuf {
    if let Ok(root) = std::env::var("LOOMA_ROOT") {
        return PathBuf::from(root).join("status.json");
    }
    PathBuf::from("/Library/Application Support/Looma/status.json")
}

/// Ключ узла лежит рядом с его данными, а не в настройках панели: агент
/// работает и без неё, в том числе когда в систему никто не вошёл.
fn key_path() -> PathBuf {
    state_path().with_file_name("join.key")
}

/// Хвост лога агента.
///
/// Читается панелью, потому что иначе его читать неоткуда: у владельца машины
/// нет ни терминала под рукой, ни причин его открывать, а когда узел не
/// поднимается — это первое и единственное место, где написано почему.
#[tauri::command]
fn agent_log(lines: usize) -> Result<String, String> {
    let path = std::path::Path::new("/Library/Logs/Looma/agent.log");
    match fs::read_to_string(path) {
        Ok(text) => {
            let all: Vec<&str> = text.lines().collect();
            let tail = all.len().saturating_sub(lines.clamp(1, 2000));
            Ok(all[tail..].join("\n"))
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            Ok(format!("лога ещё нет ({})", path.display()))
        }
        Err(e) => Err(format!("{}: {e}", path.display())),
    }
}

/// Пока этот файл есть, узел работы не берёт.
fn pause_path() -> PathBuf {
    state_path().with_file_name("paused")
}

/// Остановить или снова разрешить узел.
///
/// Файлом, а не остановкой демона: тот системный, и трогать его может только
/// root — то есть панели пришлось бы спрашивать пароль при каждом нажатии.
/// Каталог узла открыт группе admin на запись, и этого достаточно.
///
/// Агент видит файл сам: в работе — уходит на перезапуск, слив задачи, а при
/// старте ждёт, пока файл уберут. Ключ при этом никуда не девается, поэтому
/// «включить обратно» — то же одно нажатие.
#[tauri::command]
fn set_paused(paused: bool) -> Result<(), String> {
    write_pause(&pause_path(), paused)
}

fn write_pause(path: &std::path::Path, paused: bool) -> Result<(), String> {
    if paused {
        if let Some(dir) = path.parent() {
            fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
        }
        fs::write(path, "остановлено из панели\n")
            .map_err(|e| format!("не удалось остановить узел: {e}"))
    } else {
        match fs::remove_file(path) {
            Ok(()) => Ok(()),
            // Уже снят — значит нужное состояние достигнуто, а не ошибка.
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(e) => Err(format!("не удалось запустить узел: {e}")),
        }
    }
}

/// Записать ключ, который провайдер получил в панели оператора.
///
/// Права 0600: строка даёт распоряжаться узлом от имени владельца. Владельцем
/// файла становится тот, кто ввёл ключ, а агент работает под root и прочитает
/// его в любом случае.
#[tauri::command]
fn save_key(key: String) -> Result<(), String> {
    write_key(&key_path(), &key)
}

fn write_key(path: &std::path::Path, key: &str) -> Result<(), String> {
    let key = key.trim().to_string();
    // Ровно одна проверка, и та — про опечатку. Разбирать ключ здесь значило
    // бы держать вторую копию его формата, которая однажды разойдётся с
    // первой; всё остальное скажет сам агент, и скажет точнее.
    if !key.starts_with("looma_") {
        return Err("ключ начинается с looma_ — скопируйте его целиком".into());
    }
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    }
    fs::write(path, format!("{key}\n"))
        .map_err(|e| format!("не удалось сохранить ключ в {}: {e}", path.display()))?;
    let _ = fs::set_permissions(path, PermissionsExt::from_mode(0o600));
    Ok(())
}

/// Состояние узла, как его назвал агент.
///
/// Отдаётся как есть, разбирает его сторона окна: панель не должна ломаться
/// от того, что агент новее и добавил поле.
/// Насколько снимок агента может отстать, оставаясь правдой. Агент пишет его
/// раз в несколько секунд; минута — это уже другая жизнь узла.
const STALE_AFTER_S: f64 = 60.0;

#[tauri::command]
fn node_status() -> Result<serde_json::Value, String> {
    let mut status = read_status(&state_path())?;
    // Протухший снимок хуже отсутствующего: он показывает железо и «берёт
    // работу» от версии, которая давно не работает. Со стенда: панель уверяла,
    // что узел принимает задачи, а он отказывался от всех.
    if is_stale(&status) {
        status = serde_json::json!({ "running": false });
    }
    // Поверх — то, что знает пусковой слой: какая версия запущена и работает
    // ли она. Он в пакете и меняется только с ним, а снимок агента пишет лишь
    // тот агент, который это умеет: приезжающий по сети может и не уметь, и
    // тогда его снимок остаётся вечно старым. Со стенда: панель показывала
    // версию 0.1.0 и «агент замолчал», пока работал 0.1.13.
    let agent_silent = is_stale(&read_status(&state_path()).unwrap_or_default());
    if let Ok(from_launcher) = read_status(&state_path().with_file_name("launcher.json")) {
        if let (Some(target), Some(source)) =
            (status.as_object_mut(), from_launcher.as_object())
        {
            for key in ["agent_version", "paused", "why"] {
                if let Some(value) = source.get(key) {
                    target.insert(key.into(), value.clone());
                }
            }
            // "running" от пускового слоя значит только «процесс жив». Агент,
            // переставший отчитываться, — это ещё живой процесс, и панель
            // показывала бы зелёное, пока оркестратор считает узел молчащим.
            // Со стенда: снимок агента отстал на десять минут, а в окне было
            // «Узел работает».
            if agent_silent {
                target.insert("running".into(), false.into());
                target.insert("why".into(),
                    "агент запущен, но перестал отчитываться — смотрите журнал".into());
            } else if let Some(value) = source.get("running") {
                target.insert("running".into(), value.clone());
            }
            if let Some(when) = source.get("updated_at") {
                target.insert("updated_at".into(), when.clone());
            }
        }
    }
    // Введён ли ключ — знает не агент, а файловая система: без ключа агент
    // просто не запускается, и спросить его об этом некого.
    if let Some(map) = status.as_object_mut() {
        map.insert("key_present".into(), key_path().exists().into());
        // По файлу, а не по тому, что сказал агент: между решением остановиться
        // и последним снимком есть промежуток, и в нём панель показывала бы
        // работающий узел, который уже уходит.
        map.insert("paused".into(), pause_path().exists().into());
    }
    Ok(status)
}

fn is_stale(status: &serde_json::Value) -> bool {
    let Some(when) = status.get("updated_at").and_then(|v| v.as_f64()) else {
        // Нет отметки времени — значит это заглушка «агент ещё не сообщал», а
        // не устаревший снимок.
        return false;
    };
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    now - when > STALE_AFTER_S
}

fn read_status(path: &std::path::Path) -> Result<serde_json::Value, String> {
    match fs::read_to_string(path) {
        Ok(text) => serde_json::from_str(&text)
            .map_err(|e| format!("состояние узла не читается: {e}")),
        // Не ошибка, а обычное дело: агент ещё не запускался или только что
        // поставлен. Окно должно сказать это словами, а не показать пустоту.
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(serde_json::json!({
            "running": false,
            "why": format!("агент ещё не сообщал о себе ({})", path.display()),
        })),
        Err(e) => Err(format!("{}: {e}", path.display())),
    }
}

#[cfg(test)]
mod tests {
    use super::{read_status, write_key, write_pause};

    /// Агент ещё не запускался — это обычное дело, а не поломка: панель
    /// открывают сразу после установки. Ошибка вместо ответа заставила бы её
    /// показать красное там, где всё в порядке.
    #[test]
    fn missing_file_reads_as_stopped() {
        let got = read_status(std::path::Path::new("/nonexistent/looma/status.json"))
            .expect("отсутствие файла не должно быть ошибкой");
        assert_eq!(got["running"], serde_json::json!(false));
        assert!(got["why"].as_str().unwrap().contains("status.json"));
    }

    #[test]
    fn agent_snapshot_reaches_the_panel() {
        let dir = std::env::temp_dir().join("looma-panel-test");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("status.json");
        std::fs::write(&path, r#"{"running":true,"node_id":"mac-1"}"#).unwrap();

        let got = read_status(&path).unwrap();

        assert_eq!(got["running"], serde_json::json!(true));
        assert_eq!(got["node_id"], serde_json::json!("mac-1"));
    }

    ///Наполовину записанный файл поймать нельзя (агент пишет переименованием), а вот
    /// испорченный диском — можно. Панель должна сказать это словами.
    /// Опечатка ловится до записи: агент с испорченным ключом не подключится
    /// и скажет об этом в свой лог, куда провайдер не смотрит.
    #[test]
    fn a_typo_is_refused_before_writing() {
        let path = std::env::temp_dir().join("looma-key-test/join.key");
        let _ = std::fs::remove_file(&path);

        assert!(write_key(&path, "loma_abc").is_err());
        assert!(!path.exists(), "отвергнутый ключ не должен оставлять файл");
    }

    /// Строка даёт распоряжаться узлом от имени владельца, и лежит она на
    /// машине, которой пользуются и другие.
    #[test]
    fn the_key_is_written_readable_only_by_its_owner() {
        use std::os::unix::fs::PermissionsExt;

        let path = std::env::temp_dir().join("looma-key-test2/join.key");
        write_key(&path, "  looma_abc  ").unwrap();

        assert_eq!(std::fs::read_to_string(&path).unwrap(), "looma_abc\n");
        let mode = std::fs::metadata(&path).unwrap().permissions().mode();
        assert_eq!(mode & 0o077, 0, "ключ читается кем-то ещё");
    }

    /// Повторное нажатие не должно быть ошибкой: панель опрашивает состояние
    /// раз в две секунды, и между нажатием и обновлением человек успевает
    /// нажать ещё раз.
    #[test]
    fn pausing_is_repeatable() {
        let path = std::env::temp_dir().join("looma-pause-test/paused");
        let _ = std::fs::remove_file(&path);

        write_pause(&path, true).unwrap();
        write_pause(&path, true).unwrap();
        assert!(path.exists());

        write_pause(&path, false).unwrap();
        write_pause(&path, false).unwrap();
        assert!(!path.exists(), "снятие паузы дважды не должно падать");
    }

    #[test]
    fn broken_file_says_so() {
        let dir = std::env::temp_dir().join("looma-panel-test");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("broken.json");
        std::fs::write(&path, "{ не json").unwrap();

        assert!(read_status(&path).is_err());
    }
}

/// Показать панель и вывести её вперёд.
///
/// Одним местом на оба входа — меню-бар и щелчок по значку в Доке. Порознь
/// они разъезжаются: окно, которое `show()` вернул из скрытых, остаётся позади
/// активного приложения, и выглядит это как «щёлкнул, и ничего не произошло».
fn show_panel(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

/// Уходим ли мы по-настоящему.
///
/// Различает два «выхода», которые в macOS выглядят одинаково: cmd+Q и пункт
/// «Выйти» в меню-баре. Первый должен прятать окно — узел работает дальше, и
/// значок обязан остаться на месте, иначе панель не вернуть иначе как запуском
/// из Программ. Второй — единственный способ убрать приложение совсем.
static QUITTING: AtomicBool = AtomicBool::new(false);

fn main() {
    let app = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![node_status, save_key, set_paused, agent_log])
        .setup(|app| {
            let show = MenuItem::with_id(app, "show", "Показать панель", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Выйти", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            TrayIconBuilder::new()
                .menu(&menu)
                // Тот же знак, что на сайте, но без подложки и одним цветом.
                // `as_template` означает «красьте сами»: macOS сделает его
                // белым на тёмной панели и чёрным на светлой, и он останется
                // читаемым при смене темы, чего фиксированный белый не умеет.
                .icon(tauri::image::Image::from_bytes(include_bytes!("../icons/tray.png"))?)
                .icon_as_template(true)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_panel(app),
                    "quit" => {
                        QUITTING.store(true, Ordering::SeqCst);
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // Закрытие окна прячет панель, а не выключает её: узел продолжает
            // работать, и провайдер вправе просто убрать окно с глаз.
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("панель не запустилась");

    app.run(|handle, event| match event {
        // Щелчок по значку в Доке. Без этого обработчика приложение, у которого
        // все окна спрятаны, на щелчок не отвечает ничем: macOS считает, что
        // показывать нечего, и молча ничего не делает.
        tauri::RunEvent::Reopen { .. } => show_panel(handle),
        // cmd+Q прячет панель, а не закрывает её. Агент от неё не зависит и
        // продолжает работать — но значок в меню-баре должен остаться, иначе
        // вернуть окно можно только запуском из Программ.
        tauri::RunEvent::ExitRequested { api, .. } if !QUITTING.load(Ordering::SeqCst) => {
            api.prevent_exit();
        }
        _ => {}
    });
}
