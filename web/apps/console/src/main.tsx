import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toasts } from "@looma/ui";
import { App } from "./App";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, refetchOnReconnect: true } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Toasts><App /></Toasts>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
