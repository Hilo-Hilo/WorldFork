import InputPage, { type InputState, type ErrorKind } from "@/components/InputPage";

const STATES = new Set<InputState>(["empty", "loading", "error"]);
const ERROR_KINDS = new Set<ErrorKind>(["parse", "llm"]);

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ state?: string; error?: string }>;
}) {
  const { state, error } = await searchParams;
  const initialState: InputState = STATES.has(state as InputState) ? (state as InputState) : "empty";
  const errorKind: ErrorKind = ERROR_KINDS.has(error as ErrorKind) ? (error as ErrorKind) : "parse";
  return <InputPage initialState={initialState} errorKind={errorKind} />;
}
