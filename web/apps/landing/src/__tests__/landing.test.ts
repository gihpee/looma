import { describe, expect, it } from "vitest";
import { splitThink } from "../Demo";
import { cheapestRival, heroClass, savePercent, savingsRange } from "../pricing";
import type { PublicPricing } from "@looma/api";

const pricing: PublicPricing = {
  as_of: "2026-09-20", currency: "RUB", competitors: { selectel: "Selectel", aws: "AWS" }, models: [],
  gpu_classes: [
    { id: "a", name: "RTX 4090", vendor: "NVIDIA", vram_gb: 24, rate_kopecks: 12000, competitors: { selectel: 31000 } },
    { id: "b", name: "A100", vendor: "NVIDIA", vram_gb: 80, rate_kopecks: 19000, competitors: { selectel: 52000, aws: 48000 }, featured: true },
    { id: "c", name: "H100", vendor: "NVIDIA", vram_gb: 80, rate_kopecks: 0, competitors: { selectel: 64000 } },
  ],
};

describe("прайс", () => {
  it("экономия считается от самого дешёвого конкурента", () => {
    expect(cheapestRival(pricing.gpu_classes[1])).toEqual({ key: "aws", kopecks: 48000 });
    expect(savePercent(pricing.gpu_classes[1])).toBe(60);
  });
  it("hero — помеченный класс; классы без ставки не участвуют", () => {
    expect(heroClass(pricing)?.id).toBe("b");
    expect(savingsRange(pricing)).toEqual([2.5, 2.6]);
    expect(heroClass(null)).toBeNull();
  });
});

describe("splitThink", () => {
  it("делит поток на рассуждение и ответ, даже когда тег ещё не закрыт", () => {
    expect(splitThink("Привет")).toEqual({ think: "", answer: "Привет", thinking: false });
    expect(splitThink("<think>думаю")).toEqual({ think: "думаю", answer: "", thinking: true });
    expect(splitThink("<think>думаю</think>\n\nОтвет")).toEqual({ think: "думаю", answer: "Ответ", thinking: false });
  });
});
