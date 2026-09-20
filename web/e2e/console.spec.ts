import { expect, test } from "@playwright/test";
import { mockApi, signIn, world } from "./api";

test.describe("вход", () => {
  test("неверный пароль остаётся на форме с причиной", async ({ page }) => {
    await mockApi(page, world());
    await page.goto("/");
    await page.getByLabel("Почта").fill("client@looma.ru");
    await page.getByLabel("Пароль").fill("не тот");
    await page.getByRole("button", { name: "Войти" }).click();
    await expect(page.getByText("почта или пароль не подходят")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Вход в консоль" })).toBeVisible();
  });

  test("верный пароль открывает главную с балансом", async ({ page }) => {
    await mockApi(page, world());
    await signIn(page);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Иван");
    // Баланс — в шапке: 1 250 000 копеек → 12 500 ₽ (пробел может быть неразрывным).
    await expect(page.getByRole("link", { name: /Баланс|12.?500/ }).first()).toContainText(/12.?500/);
  });
});

test.describe("выпуск ключа", () => {
  test("ключ показывается один раз и появляется в списке", async ({ page }) => {
    const w = world();
    await mockApi(page, w);
    await signIn(page);
    await page.goto("/intelligence/keys");
    await page.getByRole("button", { name: "Создать ключ" }).first().click();
    await page.getByLabel("Имя").fill("прод-бот");
    await page.getByRole("button", { name: "Создать", exact: true }).click();
    await expect(page.getByText("Сохраните ключ сейчас")).toBeVisible();
    await expect(page.locator(".lu-code", { hasText: /lk_/ }).first()).toBeVisible();
    await expect(page.getByText("прод-бот").first()).toBeVisible();
    expect(w.keys).toHaveLength(1);
  });
});

test.describe("аренда кластера", () => {
  test("визард доводит до «Проверки» со стоимостью и запускает", async ({ page }) => {
    const w = world();
    await mockApi(page, w);
    await signIn(page);
    await page.goto("/compute/clusters/new");
    await expect(page.getByText("Шаг 1 из 3")).toBeVisible();
    // Карты в сети видны только после входа и только по классам.
    await expect(page.getByText("RTX 4090").first()).toBeVisible();
    await page.getByRole("button", { name: "Далее" }).click();
    await expect(page.getByText("Шаг 2 из 3")).toBeVisible();
    await page.getByLabel("Метка").fill("перебор");
    await page.getByRole("button", { name: "Далее" }).click();
    await expect(page.getByText("Шаг 3 из 3")).toBeVisible();
    // 2 узла × 120 ₽ × 6 ч = 1 440 ₽ — стоимость видна до действия.
    await expect(page.getByText(/1.?440/).first()).toBeVisible();
    await expect(page.locator(".lu-notice--ok", { hasText: "Свободных узлов хватает" })).toBeVisible();
    await page.getByRole("button", { name: "Запустить кластер" }).click();
    await expect(page).toHaveURL(/\/compute\/clusters\/g-e2e-1$/);
    expect(w.rented[0]).toMatchObject({ size: 2, hours: 6, label: "перебор", policy: "displace" });
  });

  test("при нехватке узлов политика «ждать» меняет кнопку на очередь", async ({ page }) => {
    const w = world();
    w.nodes = w.nodes.map((n) => ({ ...n, state: "inference" }));
    await mockApi(page, w);
    await signIn(page);
    await page.goto("/compute/clusters/new");
    await page.getByRole("button", { name: "Далее" }).click();
    await page.getByRole("button", { name: "Далее" }).click();
    await page.getByText("Ждать свободные").click();
    await expect(page.getByRole("button", { name: "Встать в очередь" })).toBeVisible();
  });
});
