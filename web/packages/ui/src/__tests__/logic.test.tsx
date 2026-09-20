import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { displacement } from "../network";
import { duration, money, plural, rubles, tokensK } from "../format";
import { NumberStepper } from "../primitives";

// ICU ставит неразрывный узкий пробел между разрядами — для глаз это пробел.
const sp = (s: string) => s.replace(/[\u202f\u00a0]/g, " ");

const nodes = [
  { id: "a", state: "free" as const }, { id: "b", state: "free" as const },
  { id: "c", state: "inference" as const }, { id: "d", state: "inference" as const },
  { id: "e", state: "busy" as const },
];

describe("displacement", () => {
  it("хватает свободных — никого не двигаем", () => {
    const { preview, free, short } = displacement(nodes, 2);
    expect(free).toBe(2); expect(short).toBe(0);
    expect(preview.map((n) => n.state)).toEqual(["mine", "mine", "inference", "inference", "busy"]);
  });
  it("не хватает — берём из-под инференса ровно столько, сколько не хватило", () => {
    const { preview, short } = displacement(nodes, 3);
    expect(short).toBe(1);
    expect(preview.map((n) => n.state)).toEqual(["mine", "mine", "displaced", "inference", "busy"]);
  });
  it("занятые другим не трогаем даже при нехватке", () => {
    const { preview, short } = displacement(nodes, 5);
    expect(short).toBe(3);
    expect(preview[4].state).toBe("busy");
  });
});

describe("format", () => {
  it("деньги из копеек по-русски", () => {
    expect(sp(money(123450))).toBe("1 234,50 ₽");
    expect(sp(rubles(1860))).toBe("1 860 ₽");
  });
  it("длительность", () => {
    expect(duration(42)).toBe("42 с"); expect(duration(18 * 60)).toBe("18 мин");
    expect(duration(4 * 3600 + 12 * 60)).toBe("4 ч 12 мин"); expect(duration(6 * 3600)).toBe("6 ч");
  });
  it("склонение и токены", () => {
    expect(plural(1, ["узел", "узла", "узлов"])).toBe("1 узел");
    expect(plural(3, ["узел", "узла", "узлов"])).toBe("3 узла");
    expect(sp(plural(11, ["узел", "узла", "узлов"]))).toBe("11 узлов");
    expect(tokensK(1_800_000)).toBe("1,8 M");
  });
});

describe("NumberStepper", () => {
  it("держит границы и не ломается от мусора", () => {
    let value = 6;
    const onChange = (v: number) => { value = v; };
    const { rerender } = render(<NumberStepper value={value} onChange={onChange} min={1} max={24} />);
    fireEvent.click(screen.getByLabelText("Больше")); expect(value).toBe(7);
    rerender(<NumberStepper value={24} onChange={onChange} min={1} max={24} />);
    expect(screen.getByLabelText("Больше")).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "abc" } }); expect(value).toBe(7);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "99" } }); expect(value).toBe(24);
  });
});
