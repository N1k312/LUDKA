import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { Scanner } from "./Scanner";
import "./styles.css";

// ?scan=1 — режим сканера промокодов для админа клуба.
const isScanner = new URLSearchParams(window.location.search).has("scan");

createRoot(document.getElementById("root")!).render(
  <StrictMode>{isScanner ? <Scanner /> : <App />}</StrictMode>,
);
