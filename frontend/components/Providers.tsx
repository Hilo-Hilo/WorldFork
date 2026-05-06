"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Live-feel polling: 2s default refetch for things that change during a run.
            // Per-query overrides set this to 0 (off) for static fetches like report markdown.
            refetchInterval: 2000,
            refetchIntervalInBackground: false,
            staleTime: 1000,
            retry: 1,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
