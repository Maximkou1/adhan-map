# adhan-map
Interactive world map visualizing active adhans from nealry 100k mosques with real-time prayer zone calculations.

# Methodology
The calculation logic relies on the Muslim World League (MWL) convention to determine solar angles. It utilizes a fixed 5-minute interval for each Adhan and does not account for the observer's altitude or extreme high-latitude cases, providing a generalized global approximation.

The program calculates the solar position based on the current UTC moment to generate geographic zones for Fajr, Dhuhr, Asr, Maghrib, and Isha. Simultaneously, the status of each mosque is determined independently by evaluating its coordinates against UTC time.

# Data
The geolocation and metadata for 98.500 mosques are sourced from the OpenStreetMap community via the [Layercake](https://openstreetmap.us/our-work/layercake/).
