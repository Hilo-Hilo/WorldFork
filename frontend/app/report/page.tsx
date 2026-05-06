import ReportPage from "@/components/ReportPage";

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ run?: string; version?: string }>;
}) {
  const { run, version } = await searchParams;
  return <ReportPage runId={run} initialVersionId={version} />;
}
