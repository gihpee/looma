import { createRoot } from "react-dom/client";
import { App } from "./App";
// Оформление админки, а не своё: у провайдера и у оператора должен быть один
// продукт, а не два похожих.
import "../../web/src/theme.css";
import "./panel.css";

createRoot(document.getElementById("root")!).render(<App />);
