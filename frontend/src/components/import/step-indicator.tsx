"use client";

import { Check } from "lucide-react";

interface StepIndicatorProps {
  steps: string[];
  currentStep: number;
  completedSteps: number[];
}

export function StepIndicator({
  steps,
  currentStep,
  completedSteps,
}: StepIndicatorProps) {
  return (
    <nav aria-label="Progress" className="w-full">
      <ol className="flex items-center">
        {steps.map((label, i) => {
          const isCompleted = completedSteps.includes(i);
          const isCurrent = i === currentStep;
          const isLast = i === steps.length - 1;

          return (
            <li
              key={i}
              className={isLast ? "flex-shrink-0 relative" : "flex-1 relative"}
            >
              <div className="flex items-center">
                {/* Step circle */}
                <div className="flex flex-col items-center">
                  <div
                    className="flex h-8 w-8 items-center justify-center rounded-full border-2 transition-all duration-200"
                    style={
                      isCompleted
                        ? {
                            borderColor: "var(--accent)",
                            background: "var(--accent)",
                            color: "#fff",
                            boxShadow: "var(--shadow-xs)",
                          }
                        : isCurrent
                          ? {
                              borderColor: "var(--accent)",
                              background: "var(--bg-primary)",
                              color: "var(--accent)",
                              boxShadow: "0 0 0 3px var(--accent-light)",
                            }
                          : {
                              borderColor: "var(--border-primary)",
                              background: "var(--bg-primary)",
                              color: "var(--text-tertiary)",
                            }
                    }
                  >
                    {isCompleted ? (
                      <Check className="h-4 w-4" strokeWidth={2.5} />
                    ) : (
                      <span className="text-xs font-semibold">{i + 1}</span>
                    )}
                  </div>
                  <span
                    className="mt-2 text-[11px] font-medium whitespace-nowrap absolute top-full pt-1"
                    style={{
                      color: isCurrent
                        ? "var(--accent-text)"
                        : isCompleted
                          ? "var(--text-secondary)"
                          : "var(--text-tertiary)",
                    }}
                  >
                    {label}
                  </span>
                </div>

                {/* Connector line */}
                {!isLast && (
                  <div className="ml-2 mr-2 flex-1" style={{ height: 2 }}>
                    <div
                      className="h-full w-full rounded-full transition-colors duration-300"
                      style={{
                        background: isCompleted
                          ? "var(--accent)"
                          : "var(--border-primary)",
                      }}
                    />
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
