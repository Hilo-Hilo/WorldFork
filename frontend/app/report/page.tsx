import ReportPage from "@/components/ReportPage";

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ run?: string }>;
}) {
  const { run } = await searchParams;
  return <ReportPage runId={run} />;
}
