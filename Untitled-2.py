#print("18+24")
# das ist rightich
#print(3**12 - 256**2)

import math


days_per_year = 365
days_per_week = 7

# Canculating weeks per year
weeks_per_year = days_per_year / days_per_week
#print(weeks_per_year)

# Calculating days per month
days_per_month = days_per_year / 12
#print(days_per_month)

def expected_cost(beds, baths):
    value = 20000+(5000*beds)+(2000*baths)
    return value

#print("Approximate estimate cost of a 2 bedroom and 1 bathroom house is ",expected_cost(2, 1), "$")

def get_actual_cost(sqft_walls, sqft_ceiling, sqft_per_gallon, cost_per_gallon):
    cost = math.ceil((sqft_walls+sqft_ceiling)/sqft_per_gallon)*cost_per_gallon
    gallons = math.ceil((sqft_walls+sqft_ceiling)/sqft_per_gallon)
    print(gallons, "gallons used")
    return cost
print()
x = 13.5234523626
y="summer vibe"
z=True
cost = get_actual_cost(432, 144, 400, 15) 

x_rounded = round(x, 3)
print(x_rounded)
b = len(str(x_rounded))==6

print(b)

def cost_of_project(engraving, solid_gold):
    if solid_gold == True:
        cost = len(engraving)*10
    else: cost = len(engraving)*7
    return cost

print(cost_of_project("hellotattaa", True))