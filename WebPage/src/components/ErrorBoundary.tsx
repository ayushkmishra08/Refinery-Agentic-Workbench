/**
 * A render error on one page must not blank the whole workstation.
 *
 * Without this, a single bad field access unmounts the entire React tree: the sidebar, the
 * clearance chips, the sign-out button — everything — and the person is left looking at an empty
 * viewport with no way to tell whether the server died, the session expired, or a page has a bug.
 * That is what happened on the Security route, the one only administrators can reach. With it,
 * the page that failed says so, in place, and every other route still works.
 */
import { AlertTriangle, RotateCcw } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";

import { Button, Panel } from "@/ui";

interface Props {
  /** Named in the message, so "the Security page could not be drawn" rather than a stack trace. */
  name: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class PageErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // the console is where a developer will look; the panel below is what the person sees
    console.error(`[${this.props.name}] render failed`, error, info.componentStack);
  }

  private reset = () => this.setState({ error: null });

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    return (
      <Panel className="mx-auto mt-6 max-w-xl px-5 py-5" role="alert">
        <div className="flex gap-3">
          <AlertTriangle className="mt-0.5 size-5 shrink-0 text-[var(--danger)]" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-foreground">The {this.props.name} page could not be drawn</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Something in this page's data did not match what it expected. Nothing else is affected —
              the other pages still work, and your session is intact.
            </p>
            <p className="mt-2 rounded-md bg-slate-900/[0.05] px-2.5 py-1.5 font-mono text-[0.7rem] text-slate-700">
              {this.state.error.message}
            </p>
            <div className="mt-3">
              <Button size="sm" variant="outline" onClick={this.reset}>
                <RotateCcw className="size-3.5" /> Try again
              </Button>
            </div>
          </div>
        </div>
      </Panel>
    );
  }
}
