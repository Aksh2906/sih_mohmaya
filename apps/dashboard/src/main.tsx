import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { LanguageSelector } from "./i18n";
import "./styles.css";
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <LanguageSelector />
    <App />
  </React.StrictMode>,
);
