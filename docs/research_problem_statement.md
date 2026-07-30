# Research Problem Statement

## Title

Promotion-Aware Personalized Meal Planning from Retail Grocery Data

## Problem Statement

Modern food planning tools often operate with incomplete or unrealistic data. They may ignore live retail pricing, fail to model supermarket promotions correctly, overlook user-specific nutritional context, or generate recipes without regard for what a user already has available. As a result, recommendations may be nutritionally misaligned, financially inefficient, culturally irrelevant, or operationally impractical.

This project addresses that gap by building a local supermarket intelligence system, starting with Albert Heijn, that combines four layers of information:

1. a local retailer product catalog
2. nutrition data at product level
3. promotion-aware price and basket-cost logic
4. user-profile and goal-conditioned recipe planning using an LLM

The central problem is not only to retrieve grocery data, but to make it useful for downstream planning. A meaningful planner must answer questions such as:

- What can a person cook from the ingredients they already have or plan to buy?
- How does a health goal such as weight loss, maintenance, muscle gain, or higher protein intake change what should be recommended?
- How should estimated BMR, TDEE, and other physiological context shape calorie and macro guidance?
- How much do promotions and substitutions change the real cost of following a suggested meal plan?
- Can recipe suggestions remain culturally and dietarily appropriate while also respecting cost and pantry constraints?

The system developed so far is designed to answer these questions from a realistic data foundation. It constructs a local AH knowledge base through verified API-based ingestion, stores nutrition and price snapshots, models promotion mechanics such as bundle discounts and `1+1 gratis`, and provides saved ingredient planning tied to user goals and profile data. On top of that deterministic layer, an LLM is used for explanation, goal interpretation, and recipe synthesis.

The research problem can therefore be stated as follows:

How well can a locally maintained, promotion-aware grocery knowledge base support personalized, goal-conditioned, and cost-aware recipe planning when combined with deterministic nutritional computation and LLM-based reasoning?

## Research Questions

1. How accurately can a local grocery knowledge base be constructed from retailer API data for products, prices, promotions, and nutrition?
2. How much does promotion-aware pricing change basket-cost estimates compared with naive per-unit price aggregation?
3. How much do health goals such as weight loss, maintenance, muscle gain, or increased protein intake affect recipe recommendations when the ingredient set is fixed?
4. Does including physiological context such as BMI, BMR, and TDEE improve the specificity and plausibility of calorie and macro guidance?
5. Does adding culture and cuisine preference improve the perceived relevance of generated recipes?
6. Can an LLM generate useful recipes from a saved pantry or shopping list while clearly identifying missing ingredients?
7. How much money can be saved through promotion-aware substitutions without materially degrading nutritional or goal alignment?
8. What impact does incomplete nutrition coverage have on downstream recipe quality and planning reliability?

## Hypotheses

- **H1**: Promotion-aware basket pricing produces materially different total-cost estimates than naive price aggregation.
- **H2**: Goal-conditioned recipe planning produces measurably different calorie and macro guidance than goal-agnostic planning.
- **H3**: Including BMI, BMR, and TDEE leads to more specific and more appropriate nutrition targets.
- **H4**: Culture and cuisine preference increase perceived relevance and acceptability of recipe suggestions.
- **H5**: Promotion-aware substitutions can reduce projected meal cost while preserving primary goal fit.
- **H6**: A deterministic nutrition-and-price layer combined with LLM reasoning performs better than an LLM-only planning approach for cost and nutrition reliability.

## Methodology

### 1. Knowledge Base Construction

Use the current Albert Heijn ingestion pipeline to maintain a local grocery knowledge base containing:

- product metadata
- nutrition facts and row-level nutrition entries
- price snapshots
- bonus and promotion metadata
- effective basket pricing behavior

This layer provides the deterministic ground truth on which planning is built.

### 2. User Scenario Design

Define structured planning scenarios that vary across:

- primary goal: lose weight, maintain weight, gain muscle, eat more protein, save money, eat more plants
- secondary goal: especially cost-oriented goals such as saving money
- physiology: age, gender, height, weight
- activity context: sedentary to very active lifestyle
- diet constraints: vegetarian, vegan, omnivore, pescatarian, poultry-focused, meat-based
- culture and cuisine preference
- allergy and exclusion constraints

### 3. Ingredient Plan Construction

Build saved ingredient lists using the local catalog for scenarios such as:

- pantry-only planning
- planned purchases for the next day
- weekly meal-prep ingredient sets
- bonus-heavy shopping lists
- low-cost planning cases
- incomplete ingredient lists that require targeted missing-item reasoning

### 4. Deterministic Profile Computation

Use code-based calculations to estimate:

- BMI from height and weight
- BMR from age, gender, height, and weight
- TDEE from BMR and lifestyle/activity multiplier

These values should be displayed directly to the user and passed to the LLM as structured context.

### 5. LLM-Based Planning

Prompt the model with:

- the saved ingredient plan
- the user’s goals and profile
- culture and cuisine preferences
- deterministic price and nutrition context
- explicit pantry-staple assumptions

Require structured responses that include:

- goal-fit explanation
- metabolic interpretation using BMR and TDEE
- calorie and macro guidance
- recipe suggestions
- missing ingredients
- cost-aware substitutions
- savings and nutrition notes

### 6. Comparative Evaluation

Compare multiple system configurations:

- no goal context
- goal-only context
- goal plus deterministic metabolic metrics
- goal plus metabolic metrics plus culture/cuisine preference
- goal plus cost and bonus awareness
- deterministic + LLM system versus an LLM-only baseline

## Evaluation Metrics

### Data Quality Metrics

- product coverage
- nutrition coverage
- duplicate rate
- price snapshot freshness
- promotion-logic correctness

### Basket and Cost Metrics

- total cost with current price
- total cost without bonus
- estimated savings from promotions
- estimated savings from substitutions

### Nutrition and Planning Metrics

- calorie-target consistency with selected goal
- protein-target consistency
- presence and quality of sugar and salt guidance
- ingredient-plan adherence
- missing-item count

### Recipe Quality Metrics

- feasibility from saved ingredients
- dietary compatibility
- allergy safety
- cultural relevance
- explanation clarity

### User-Centered Metrics

- perceived usefulness
- perceived trustworthiness
- perceived cultural fit
- likelihood of cooking suggested meals

## Expected Contribution

This work contributes a practical framework for combining retailer data engineering, deterministic nutrition and pricing logic, and LLM-based reasoning into a unified personalized food-planning system. The main contribution is not merely recipe generation, but the integration of realistic supermarket conditions, physiological guidance, and goal-aware personalization into one reproducible pipeline.

## Future Work

Future research can extend this framework by:

- incorporating multiple supermarkets
- comparing cross-retailer substitutions
- improving long-tail nutrition completeness
- adding household-level and multi-person planning
- optimizing weekly or monthly budgets
- tracking meal history and user preferences over time
- introducing uncertainty estimates for incomplete or inferred data
- conducting longitudinal user studies on adherence and satisfaction
