import DashboardPage from "@/components/DashboardPage";

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ orientation?: string; scores?: string }>;
}) {
  const { orientation, scores } = await searchParams;
  const o: "horizontal" | "vertical" = orientation === "vertical" ? "vertical" : "horizontal";
  const showScores = scores !== "off";
  return <DashboardPage orientation={o} showScores={showScores} />;
}
