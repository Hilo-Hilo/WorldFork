import DashboardPage from "@/components/DashboardPage";

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ run?: string; orientation?: string; scores?: string }>;
}) {
  const { run, orientation, scores } = await searchParams;
  const o: "horizontal" | "vertical" = orientation === "horizontal" ? "horizontal" : "vertical";
  const showScores = scores !== "off";
  return <DashboardPage runId={run} orientation={o} showScores={showScores} />;
}
