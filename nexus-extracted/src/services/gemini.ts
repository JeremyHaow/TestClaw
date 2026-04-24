import { GoogleGenAI, Type } from "@google/genai";

const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });

export async function generateTestCases(input: string, source: 'swagger' | 'doc') {
  const prompt = source === 'swagger' 
    ? `Generate comprehensive test cases for the following Swagger/OpenAPI definition. Include positive, negative, and boundary cases. Format as JSON array of objects with 'title', 'steps', and 'expectedOutcome'. Input: ${input}`
    : `Generate comprehensive test cases for the following requirements documentation. Focus on user flows and business logic validation. Format as JSON array of objects with 'title', 'steps', and 'expectedOutcome'. Input: ${input}`;

  try {
    const response = await ai.models.generateContent({
      model: "gemini-3-flash-latest",
      contents: prompt,
      config: {
        responseMimeType: "application/json",
        responseSchema: {
          type: Type.ARRAY,
          items: {
            type: Type.OBJECT,
            properties: {
              title: { type: Type.STRING },
              steps: { type: Type.ARRAY, items: { type: Type.STRING } },
              expectedOutcome: { type: Type.STRING }
            }
          }
        }
      }
    });

    return JSON.parse(response.text);
  } catch (error) {
    console.error("AI Generation Error:", error);
    throw error;
  }
}
