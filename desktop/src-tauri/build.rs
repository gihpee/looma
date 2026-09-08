use std::fs;
use std::path::PathBuf;

fn main() {
    hide_build_output_from_spotlight();
    tauri_build::build()
}

/// Сказать Spotlight не заглядывать в каталог сборки.
///
/// Иначе он находит там собранные Looma.app — по одной на каждый профиль — и
/// показывает их в поиске рядом с установленной, подписывая «debug» и
/// «release». Человек видит три приложения с одним именем и не понимает, какое
/// из них настоящее; хуже того, запустить можно не то.
///
/// Метка ставится здесь, а не руками: `cargo clean` уносит её вместе с
/// каталогом, и через сборку-другую всё вернулось бы.
fn hide_build_output_from_spotlight() {
    // OUT_DIR — это target/<профиль>/build/<пакет>/out; четыре шага вверх дают
    // сам target.
    let out = match std::env::var_os("OUT_DIR") {
        Some(value) => PathBuf::from(value),
        None => return,
    };
    let target = match out.ancestors().nth(4) {
        Some(path) => path.to_path_buf(),
        None => return,
    };
    let marker = target.join(".metadata_never_index");
    if !marker.exists() {
        // Не фатально: без метки соберётся ровно то же самое, просто Spotlight
        // будет видеть лишнее.
        let _ = fs::write(&marker, "");
    }
}
