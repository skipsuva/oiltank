import cv2
from detect import detect_level

img = cv2.imread("images/20260531_062216.jpg")
result = detect_level(img)
print(result["level_label"], result["percentage"], result["confidence"])
cv2.imwrite("images/test_annotated.jpg", result["annotated_image"])
